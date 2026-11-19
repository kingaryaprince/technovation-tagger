# Speaker Bureau search (staff only)

A small password-protected website for the Speaker Bureau search. It shows a sign-in page, then the
search page. It talks to your Supabase database from the server, so the secret key never reaches
anyone's browser.

Files in this folder:
- `server.py`: the web server (sign-in, the search, the place lookup)
- `static/app.html`: the search page itself
- `requirements.txt`: empty on purpose; the app needs no extra packages

## Before you start (check once)
- `sql/005_speaker_search.sql` has been run in Supabase (it already has, if the command-line search works).
- `python3 -m alumni_tagger geocode-locations` has been run, otherwise the travel radius finds no one.

## Step 1: Put this folder in your GitHub repo (no terminal)
1. Unzip `search-app.zip` on your computer so you have a folder called `search-app`.
2. Open your `technovation-tagger` repo on github.com.
3. Click **Add file**, then **Upload files**.
4. Drag the whole `search-app` folder into the box. Wait until the file list shows `search-app/server.py` and `search-app/static/app.html`.
5. Click **Commit changes**.

## Step 2: Create the site on Render
1. Sign up at render.com with your GitHub account, and allow it to see `technovation-tagger`.
2. Click **New**, then **Web Service**, then pick `technovation-tagger`.
3. Fill in:
   - **Root Directory:** `search-app`
   - **Runtime / Language:** Python 3
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python server.py`
   - **Instance Type:** Free
4. Open **Environment Variables** and add these (type them here, never in GitHub):
   - `STAFF_PASSWORD`: the shared password (at least 10 characters; four random words works well)
   - `SUPABASE_URL`: from Supabase, Project Settings, API, "Project URL"
   - `SUPABASE_SERVICE_KEY`: from the same page, the `service_role` key (a secret: treat it like a password)
   - Optional: `GEOCODER_USER_AGENT` = `TechnovationAlumniHub/1.0 (your-email@example.org)`
5. Click **Create Web Service**. When it says "Live", open the address at the top (ends in `.onrender.com`).

## Using it
- Share the address and the password with staff. Sessions last 12 hours.
- To change the password, edit `STAFF_PASSWORD` in Render. Everyone is signed out.
- Five wrong passwords in 15 minutes locks sign-in for 15 minutes.
- On the free plan the site sleeps after about 15 minutes of no use. The first visit afterwards takes up to a minute.

## Good to know
- Results show the top 50 people. Strict match, keyword, and sorting are applied to those 50.
- The shortlist lives only in the open browser tab, so contact details are never saved on a computer.
- The travel radius looks the place up on OpenStreetMap, about one lookup per second.
- Tags are grouped into five filter groups: Topic, Audience, Language, Speaking experience, Skills.
