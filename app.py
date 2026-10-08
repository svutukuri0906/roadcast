"""Roadcast I-45: live rain, visibility and wind along I-45 between Dallas and Houston.

Streamlit app. Weather from Open-Meteo (free, no key, 15-minute steps).
AI advice from Claude (secret or env ANTHROPIC_API_KEY; optional CLAUDE_MODEL).
"""

import datetime as dt
import os
from zoneinfo import ZoneInfo

import altair as alt
import pandas as pd
import pydeck as pdk
import requests
import streamlit as st

TZ = ZoneInfo("America/Chicago")
STEP = dt.timedelta(minutes=15)
MPH = 65
TOTAL_MI = 240

# Towns on I-45 with road miles from downtown Dallas
TOWNS = [
    ("Dallas", 32.7767, -96.7970, 0),
    ("Ennis", 32.3293, -96.6253, 34),
    ("Corsicana", 32.0954, -96.4689, 55),
    ("Fairfield", 31.7246, -96.1650, 92),
    ("Buffalo", 31.4635, -96.0580, 113),
    ("Madisonville", 30.9499, -95.9116, 147),
    ("Huntsville", 30.7235, -95.5508, 175),
    ("Conroe", 30.3119, -95.4561, 203),
    ("Houston", 29.7604, -95.3698, 240),
]

WMO = {
    0: "Clear", 1: "Mostly clear", 2: "Partly cloudy", 3: "Cloudy", 45: "Fog", 48: "Freezing fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle", 56: "Freezing drizzle", 57: "Freezing drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain", 66: "Freezing rain", 67: "Freezing rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Violent showers", 85: "Snow showers", 86: "Snow showers",
    95: "Thunderstorm", 96: "Thunderstorm with hail", 99: "Thunderstorm with hail",
}
VERDICT = {0: "Good to drive", 1: "Drive with caution", 2: "Wait or delay", -1: "No live data"}
LEVEL_RGB = {-1: [140, 150, 145], 0: [29, 138, 75], 1: [214, 140, 0], 2: [192, 53, 42]}
CATS = ["Dry", "Light", "Moderate", "Heavy", "Very heavy", "Snow or ice"]
CAT_COLORS = ["#e3e9e5", "#8fc6ec", "#3f8fd2", "#1c4f9c", "#7b3fb3", "#d77ad1"]

def setting(name, default=""):
    """Read from Streamlit secrets first, then environment variables."""
    try:
        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, default)


DEFAULT_MODEL = "claude-haiku-4-5-20251001"


# ---------------------------------------------------------------- data

@st.cache_data(ttl=300, show_spinner=False)
def fetch_weather():
    """One Open-Meteo call for all towns: 15-minute forecast for the next 4 hours plus current conditions."""
    params = {
        "latitude": ",".join(str(t[1]) for t in TOWNS),
        "longitude": ",".join(str(t[2]) for t in TOWNS),
        "minutely_15": "precipitation,snowfall,visibility,wind_gusts_10m,weather_code",
        "current": "temperature_2m,weather_code,visibility,wind_gusts_10m,precipitation",
        "forecast_minutely_15": 18,
        "timezone": "America/Chicago",
        "precipitation_unit": "inch",
        "wind_speed_unit": "mph",
        "temperature_unit": "fahrenheit",
    }
    r = requests.get("https://api.open-meteo.com/v1/forecast", params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        data = [data]
    return data, dt.datetime.now(TZ)


@st.cache_data(ttl=86400, show_spinner=False)
def fetch_route_path():
    """Road geometry from the public OSRM server; straight lines between towns if it's unreachable."""
    coords = ";".join(f"{t[2]},{t[1]}" for t in (TOWNS[0], TOWNS[-1]))
    try:
        r = requests.get(
            f"https://router.project-osrm.org/route/v1/driving/{coords}",
            params={"overview": "full", "geometries": "geojson"}, timeout=10,
        )
        r.raise_for_status()
        return r.json()["routes"][0]["geometry"]["coordinates"]
    except Exception:
        return [[t[2], t[1]] for t in TOWNS]


# ---------------------------------------------------------------- analysis

def to_miles(value, unit):
    if value is None:
        return None
    unit = (unit or "m").lower()
    if unit in ("ft", "feet"):
        return value / 5280
    if unit in ("km",):
        return value / 1.609
    return value / 1609.34  # metres


def intensity(rate_in_hr, snow):
    """Category, label and risk level for a 15-minute slot."""
    if snow and snow > 0:
        return 5, "snow or ice", 2
    if rate_in_hr >= 1.0:
        return 4, "very heavy rain", 2
    if rate_in_hr >= 0.3:
        return 3, "heavy rain", 2
    if rate_in_hr >= 0.1:
        return 2, "moderate rain", 1
    if rate_in_hr > 0.01:
        return 1, "light rain", 1
    return 0, "dry", 0


def town_slots(loc, now):
    """15-minute slots still ahead. Open-Meteo stamps each slot at its END (sum of the preceding 15 min)."""
    m = loc.get("minutely_15", {})
    units = loc.get("minutely_15_units", {})
    slots = []
    for i, t in enumerate(m.get("time", [])):
        end = dt.datetime.fromisoformat(t).replace(tzinfo=TZ)
        if end <= now:
            continue
        p = (m.get("precipitation") or [0])[i] or 0
        snow = (m.get("snowfall") or [0])[i] or 0
        cat, word, lv = intensity(p * 4, snow)
        vis = (m.get("visibility") or [None])[i]
        slots.append({
            "start": end - STEP, "end": end, "precip": p, "rate": p * 4, "snow": snow,
            "cat": cat, "word": word, "lv": lv,
            "vis": to_miles(vis, units.get("visibility")),
            "gust": (m.get("wind_gusts_10m") or [None])[i],
            "code": (m.get("weather_code") or [None])[i],
        })
    return slots


def rain_windows(slots, now):
    wins, cur = [], None
    for s in slots:
        if s["cat"] > 0:
            if cur is None:
                cur = {"start": s["start"], "end": s["end"], "cat": s["cat"], "word": s["word"], "lv": s["lv"]}
            else:
                cur["end"] = s["end"]
                if s["cat"] > cur["cat"]:
                    cur.update(cat=s["cat"], word=s["word"], lv=s["lv"])
        elif cur:
            wins.append(cur)
            cur = None
    if cur:
        cur["open"] = True
        wins.append(cur)
    for w in wins:
        w["in_min"] = max(0, round((w["start"] - now).total_seconds() / 60))
        w["lasts"] = round((w["end"] - max(w["start"], now)).total_seconds() / 60)
    return wins


def analyze(data, south, leave_min, now=None):
    now = now or dt.datetime.now(TZ)
    order = list(range(len(TOWNS))) if south else list(reversed(range(len(TOWNS))))
    rows = []
    for idx in order:
        name, lat, lon, mi = TOWNS[idx]
        loc = data[idx] if idx < len(data) else {}
        route_mi = mi if south else TOTAL_MI - mi
        eta_min = leave_min + round(route_mi / MPH * 60)
        eta = now + dt.timedelta(minutes=eta_min)
        slots = town_slots(loc, now)
        wins = rain_windows(slots, now)
        at = next((s for s in slots if s["end"] >= eta), None)
        cur = loc.get("current", {}) or {}
        cur_units = loc.get("current_units", {}) or {}
        rows.append({
            "name": name, "lat": lat, "lon": lon, "route_mi": route_mi, "eta_min": eta_min, "eta": eta,
            "slots": slots, "wins": wins, "at": at,
            "now_text": WMO.get(cur.get("weather_code"), "–"),
            "now_temp": cur.get("temperature_2m"),
            "now_vis": to_miles(cur.get("visibility"), cur_units.get("visibility")),
            "now_gust": cur.get("wind_gusts_10m"),
            "has_data": bool(slots) or bool(cur),
        })

    level, why = 0, []

    def bump(lv, text):
        nonlocal level
        level = max(level, lv)
        why.append(text)

    live = [r for r in rows if r["has_data"]]
    if not live:
        level = -1
    for r in live:
        a = r["at"]
        vis = a["vis"] if a and a["vis"] is not None else r["now_vis"]
        gust = a["gust"] if a and a["gust"] is not None else r["now_gust"]
        code = a["code"] if a else None
        if a and a["cat"] > 0:
            bump(a["lv"], f"{a['word']} as you pass {r['name']}")
        if code in (95, 96, 99):
            bump(2, f"thunderstorms at {r['name']}")
        if vis is not None and vis < 1:
            bump(2, f"visibility under 1 mi at {r['name']}")
        elif vis is not None and vis < 3:
            bump(1, f"visibility {vis:.1f} mi at {r['name']}")
        if gust is not None and gust >= 45:
            bump(2, f"gusts {gust:.0f} mph at {r['name']}")
        elif gust is not None and gust >= 30:
            bump(1, f"gusts {gust:.0f} mph at {r['name']}")

    first = None
    for r in live:
        for w in r["wins"]:
            if first is None or w["start"] < first[1]["start"]:
                first = (r, w)
    hits = [r for r in live if r["at"] and r["at"]["cat"] > 0]
    vis_rows = [(r, r["at"]["vis"] if r["at"] and r["at"]["vis"] is not None else r["now_vis"]) for r in live]
    vis_rows = [(r, v) for r, v in vis_rows if v is not None]
    min_vis = min(vis_rows, key=lambda x: x[1]) if vis_rows else None
    gust_rows = [(r, r["at"]["gust"] if r["at"] and r["at"]["gust"] is not None else r["now_gust"]) for r in live]
    gust_rows = [(r, g) for r, g in gust_rows if g is not None]
    max_gust = max(gust_rows, key=lambda x: x[1]) if gust_rows else None
    return {"now": now, "rows": rows, "level": level, "why": why, "first": first, "hits": hits,
            "min_vis": min_vis, "max_gust": max_gust}


def rain_headline(A):
    if not A["first"]:
        return "No rain on I-45 for the next 4 hours."
    r, w = A["first"]
    dur = "for the rest of the forecast" if w.get("open") else f"for about {w['lasts']} min"
    if w["in_min"] <= 0:
        return f"Raining now near {r['name']}: {w['word']} {dur}."
    return f"Rain starts in about {w['in_min']} min near {r['name']}: {w['word']} {dur}."


def fmt_time(t):
    return t.strftime("%-I:%M %p")


# ---------------------------------------------------------------- AI

def ai_context(A, south, leave_min):
    lines = []
    for r in A["rows"]:
        wins = "; ".join(
            f"{w['word']} {'now' if w['in_min'] <= 0 else 'starting in ~' + str(w['in_min']) + ' min'}, "
            f"lasting {'past the forecast window' if w.get('open') else '~' + str(w['lasts']) + ' min'}"
            for w in r["wins"]
        ) or "none in the next 4 hours"
        a = r["at"]
        if a:
            at = (f"when the driver passes: {a['word']}, visibility {a['vis']:.1f} mi, gusts {a['gust'] or 0:.0f} mph"
                  if a["vis"] is not None else f"when the driver passes: {a['word']}")
        else:
            at = "driver passes after the forecast window"
        now_bits = f"now {r['now_text']}, {r['now_temp']}°F" if r["now_temp"] is not None else f"now {r['now_text']}"
        lines.append(f"- {r['name']} ({r['route_mi']} mi, driver arrives in ~{r['eta_min']} min at {fmt_time(r['eta'])}): "
                     f"{now_bits}. Rain windows: {wins}. {at}.")
    trip = "Dallas to Houston" if south else "Houston to Dallas"
    return (f"Trip: {trip} on I-45, {TOTAL_MI} miles, about {round(TOTAL_MI / MPH * 60)} min at {MPH} mph. "
            f"Leaving {'now' if not leave_min else 'in ' + str(leave_min) + ' min'} (current time {fmt_time(A['now'])}).\n"
            f"Rule-based check: {VERDICT[A['level']]}"
            f"{' (' + '; '.join(A['why']) + ')' if A['why'] else ''}.\n"
            f"15-minute forecast by town, in driving order:\n" + "\n".join(lines))


def ai_prompt(A, south, leave_min):
    return (
        "You are a driving co-pilot. Using only the live data below, tell the driver in plain words:\n"
        "1. First line: a clear recommendation (Go now, Wait N minutes, or Don't drive yet) and the main reason.\n"
        "2. Then 2 to 4 bullets starting with '- ': when rain starts on the route and for how long, and where "
        "(for example 'rain starts near Ennis in about 15 min and lasts about 30 min'); whether they will actually "
        "drive through it given their arrival times; visibility and wind; and one or two practical driving suggestions.\n"
        "Use minutes and town names from the data. Under 110 words. No headings. If no rain is expected, say so plainly.\n\n"
        + ai_context(A, south, leave_min)
    )


def ai_stream(prompt, api_key, model):
    import anthropic

    client = anthropic.Anthropic(api_key=api_key)
    with client.messages.stream(
        model=model,
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        for text in stream.text_stream:
            yield text


# ---------------------------------------------------------------- UI

def route_map(A):
    path = fetch_route_path()
    towns = pd.DataFrame([{
        "name": r["name"], "lat": r["lat"], "lon": r["lon"],
        "color": LEVEL_RGB[(r["at"]["lv"] if r["at"] else (1 if r["wins"] else 0)) if r["has_data"] else -1],
        "label": f"{r['name']} {fmt_time(r['eta'])}",
    } for r in A["rows"]])
    layers = [
        pdk.Layer("PathLayer", data=[{"path": path}], get_path="path", get_color=[57, 64, 60],
                  width_min_pixels=6),
        pdk.Layer("PathLayer", data=[{"path": path}], get_path="path", get_color=LEVEL_RGB[max(A["level"], 0)],
                  width_min_pixels=3),
        pdk.Layer("ScatterplotLayer", data=towns, get_position="[lon, lat]", get_fill_color="color",
                  get_line_color=[255, 255, 255], line_width_min_pixels=2, stroked=True, radius_min_pixels=7),
        pdk.Layer("TextLayer", data=towns, get_position="[lon, lat]", get_text="label", get_size=13,
                  get_color=[22, 32, 27], get_pixel_offset=[12, 0], get_text_anchor="'start'"),
    ]
    view = pdk.ViewState(latitude=31.25, longitude=-96.1, zoom=6.1)
    return pdk.Deck(layers=layers, initial_view_state=view, map_style="light",
                    tooltip={"text": "{label}"})


def rain_timeline(A):
    rows = []
    for r in A["rows"]:
        for s in r["slots"]:
            rows.append({"Town": r["name"], "start": s["start"].replace(tzinfo=None),
                         "end": s["end"].replace(tzinfo=None), "Rain": CATS[s["cat"]],
                         "in/hr": round(s["rate"], 2)})
    if not rows:
        return None
    df = pd.DataFrame(rows)
    etas = pd.DataFrame([{"Town": r["name"], "eta": r["eta"].replace(tzinfo=None)} for r in A["rows"]])
    order = [r["name"] for r in A["rows"]]
    base = alt.Chart(df).mark_rect(stroke="white", strokeWidth=1).encode(
        x=alt.X("start:T", title=None, axis=alt.Axis(format="%-I:%M %p", labelAngle=0, tickCount=6)),
        x2="end:T",
        y=alt.Y("Town:N", sort=order, title=None),
        color=alt.Color("Rain:N", scale=alt.Scale(domain=CATS, range=CAT_COLORS),
                        legend=alt.Legend(orient="bottom", title=None)),
        tooltip=["Town", alt.Tooltip("start:T", format="%-I:%M %p", title="From"), "Rain", "in/hr"],
    )
    you = alt.Chart(etas).mark_point(shape="triangle-right", size=90, filled=True, color="#16201b").encode(
        x="eta:T", y=alt.Y("Town:N", sort=order), tooltip=[alt.Tooltip("eta:T", format="%-I:%M %p", title="You pass")],
    )
    return (base + you).properties(height=30 * len(order))


def main():
    st.set_page_config(page_title="Roadcast I-45", page_icon="🛣️", layout="centered")
    if setting("APP_PASSWORD"):
        if not st.session_state.get("unlocked"):
            pw = st.text_input("Password", type="password")
            if pw and pw == setting("APP_PASSWORD"):
                st.session_state["unlocked"] = True
                st.rerun()
            st.stop()
    st.title("Roadcast I-45")
    st.caption("Live rain, visibility and wind between Dallas and Houston")

    c1, c2 = st.columns([3, 2])
    direction = c1.radio("Direction", ["Dallas → Houston", "Houston → Dallas"], horizontal=True,
                         label_visibility="collapsed")
    leave = c2.selectbox("Leaving", [0, 15, 30, 45, 60],
                         format_func=lambda m: "Leave now" if m == 0 else f"Leave in {m} min",
                         label_visibility="collapsed")
    south = direction.startswith("Dallas")

    try:
        data, fetched = fetch_weather()
    except Exception as e:
        st.error(f"Couldn't load the forecast from Open-Meteo ({e}). If your workspace blocks outbound "
                 "internet for apps, allow api.open-meteo.com.")
        st.stop()

    A = analyze(data, south, leave)
    lv = A["level"]
    box = {0: st.success, 1: st.warning, 2: st.error}.get(lv, st.info)
    box(f"**{VERDICT[lv]}.** {rain_headline(A)}")

    if A["hits"]:
        h = A["hits"][0]
        st.write(f"You'd drive into **{h['at']['word']}** at **{h['name']}** around {fmt_time(h['eta'])}.")
    elif A["first"]:
        st.write("Your timing misses the rain in this forecast.")

    m1, m2, m3 = st.columns(3)
    if A["min_vis"]:
        r, v = A["min_vis"]
        m1.metric("Lowest visibility", f"{v:.1f} mi", r["name"], delta_color="off")
    if A["max_gust"]:
        r, g = A["max_gust"]
        m2.metric("Strongest gusts", f"{g:.0f} mph", r["name"], delta_color="off")
    end = A["rows"][-1]
    m3.metric("Arrive", fmt_time(end["eta"]), f"{end['eta_min']} min", delta_color="off")
    if A["why"]:
        st.caption("Why: " + "; ".join(A["why"][:4]))

    # AI co-pilot
    if st.button("✨ Ask AI: should I drive now?", type="primary", use_container_width=True):
        api_key = setting("ANTHROPIC_API_KEY")
        if not api_key:
            st.info("Add ANTHROPIC_API_KEY to the app's secrets to turn on AI advice.")
        else:
            try:
                st.session_state["ai"] = st.write_stream(
                    ai_stream(ai_prompt(A, south, leave), api_key, setting("CLAUDE_MODEL", DEFAULT_MODEL)))
                st.session_state["ai_for"] = (south, leave, fetched)
            except Exception as e:
                st.error(f"Claude didn't answer: {e}")
    elif st.session_state.get("ai") and st.session_state.get("ai_for") == (south, leave, fetched):
        st.markdown(st.session_state["ai"])

    st.subheader("Route")
    st.pydeck_chart(route_map(A), use_container_width=True)

    st.subheader("Rain along the way")
    st.caption("Each row is a town in driving order; ▶ marks when you pass it.")
    chart = rain_timeline(A)
    if chart is not None:
        st.altair_chart(chart, use_container_width=True)

    table = pd.DataFrame([{
        "Town": r["name"],
        "Mi": r["route_mi"],
        "You pass": fmt_time(r["eta"]),
        "Now": r["now_text"],
        "At your pass": (r["at"]["word"] if r["at"] else "after forecast"),
        "Visibility (mi)": round(r["at"]["vis"], 1) if r["at"] and r["at"]["vis"] is not None else None,
        "Gusts (mph)": round(r["at"]["gust"]) if r["at"] and r["at"]["gust"] is not None else None,
        "Next rain": (f"in {r['wins'][0]['in_min']} min, ~{r['wins'][0]['lasts']} min" if r["wins"] else "none"),
    } for r in A["rows"]])
    st.dataframe(table, hide_index=True, use_container_width=True)

    c1, c2 = st.columns([3, 1])
    c1.caption(f"Forecast updated {fmt_time(fetched)} · Open-Meteo 15-minute data · {MPH} mph, no stops")
    if c2.button("Refresh"):
        fetch_weather.clear()
        st.session_state.pop("ai", None)
        st.rerun()


if __name__ == "__main__":
    main()
