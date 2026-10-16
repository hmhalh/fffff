# Iraqi AI Community — Starter (2 personas)

A safe starter for a Telegram group with one clearly identified AI bot and two Iraqi-Arabic personas:
- Ahmed: social, playful, curious.
- Zahraa: calm, thoughtful, kind.

It uses Gemini API, SQLite persistent memory, and aiogram. It does **not** automate Telegram user accounts or use user-session files. A normal Telegram bot cannot send messages as multiple user accounts, and bots generally cannot talk to other bots as if they were users. For that reason, this starter represents personas through one bot.

## Requirements
- Python 3.11+
- A Telegram bot token from @BotFather
- A Gemini API key from Google AI Studio: https://aistudio.google.com/
- A group where the bot is added. For group message access, configure BotFather privacy mode appropriately; only do this for your own group and tell members it is an AI bot.

## Setup
1. Create a virtual environment:
   `python -m venv .venv`
2. Activate it, then install:
   `pip install -r requirements.txt`
3. Copy `.env.example` to `.env` and fill in your keys.
4. Run:
   `python app.py`

On Windows, use `.venv\\Scripts\\activate`; on Linux:
`source .venv/bin/activate`

## How to use
- Mention the bot or reply to one of its messages to get a response.
- `/personas` lists the two personas.
- `/memory` shows a short summary of stored group context.
- `/forget` clears stored conversational memory for the current chat.
- `/help` shows commands.

This starter only replies when mentioned/replied to. It does not spontaneously flood the group. Add more personas in `PERSONAS` in `app.py`. Memory is stored in `community.db` and should be treated as personal data; protect the server and database.

## Security
- Never share your Telegram bot token or Gemini API key.
- Do not upload session files, `.env`, or `community.db` to public repositories.
- Use HTTPS/network protections, OS updates, and encrypted backups on a VPS.
- Keep AI personas disclosed as AI-generated characters; don't use them to mislead people into believing they are real people.
