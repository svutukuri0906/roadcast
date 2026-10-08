# Roadcast I-45

Live rain, visibility and wind along I-45 between Dallas and Houston, with an AI co-pilot (Claude) that says whether to drive now.

- **Weather:** Open-Meteo 15-minute forecast for 9 towns on I-45 (free, no key)
- **Map:** the route with each town colored by what you'll hit when you pass it
- **Rain timeline:** towns × the next 4 hours, with a marker for when you reach each town
- **AI:** "Ask AI: should I drive now?" sends the live data to Claude for a go / wait / don't-go call

## 1. Get a Claude API key

Sign in to the Claude Console (console.anthropic.com), add a payment method under Billing, and create a key under API keys. API usage is billed separately from a Claude.ai subscription. Each "Ask AI" tap is a small request on the default Haiku model, a fraction of a cent.

## 2. Put the code on GitHub

Create a new repository (public or private) and upload:

```
app.py
requirements.txt
.gitignore
.streamlit/secrets.toml.example   (optional)
```

You can do this from the GitHub website: New repository → Add file → Upload files.

## 3. Deploy on Streamlit Community Cloud

1. Go to share.streamlit.io and sign in with GitHub.
2. Create app → pick your repository, branch `main`, main file `app.py`.
3. Open **Advanced settings → Secrets** and paste:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-your-key"
   APP_PASSWORD = "choose-something"
   ```
4. Deploy. You get a URL like `https://<name>.streamlit.app`; add it to your phone's home screen.

**Protect your key's credits:** set `APP_PASSWORD` (the app then asks for it), and/or set the app to private in its Streamlit settings so only invited emails can open it.

## Run locally

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # then add your key
streamlit run app.py
```

## Settings (secrets or environment variables)

| Name | Required | Purpose |
|---|---|---|
| `ANTHROPIC_API_KEY` | for AI | Your Claude API key |
| `CLAUDE_MODEL` | no | Model ID, default `claude-haiku-4-5-20251001` |
| `APP_PASSWORD` | no | Password gate for the app |

## Notes

- Forecast refreshes every 5 minutes or when you tap Refresh. Drive times assume 65 mph with no stops.
- Risk rules: caution for light/moderate rain where you'll be, visibility under 3 mi, or gusts 30+ mph; delay for heavy rain, thunderstorms, snow/ice, visibility under 1 mi, or gusts 45+ mph.
- Other routes: edit the `TOWNS` list (name, lat, lon, road miles from the start) and `TOTAL_MI` in `app.py`.
