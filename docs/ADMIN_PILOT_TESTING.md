# Speaker Bureau pilot: testing guide for admins

Thank you for helping test the Technovation Alumni Speaker Bureau. This tool helps staff find
alumni to speak at events, sit on panels, or share their stories. Testing takes about **20 minutes**.

Once you're done, please fill out the feedback form: **[feedback form link]**

> **About the profiles:** During the pilot, most profiles in the search are **fictional sample
> alumni** that the student team wrote for testing. They have names, stories and answers that
> look real, but none of them are real people, so please don't contact them. Real alumni will
> appear as they complete the intake form.

---

## Option A: Use the website (recommended)

You only need a web browser.

1. Open **https://technovation-alumni-search-bureau.onrender.com/**
   - The first visit can take **up to a minute** to load while the server wakes up. That's normal.
2. Enter the **staff password**. The team sends it separately; it isn't in this guide.
3. You'll stay signed in for 12 hours. Click **Sign out** at the top when you're done.

Five wrong passwords in 15 minutes lock sign-in for 15 minutes.

### What's on the page

| Part | What it does |
| --- | --- |
| **Search box** | Type a name, a topic (like "Healthcare") or any word from a bio. Suggested tags appear as you type. |
| **Filters** (left side) | Pick tags in five groups: **Topic**, **Audience**, **Language**, **Speaking experience** and **Skills**. |
| **Location and travel** | Enter the event's city and a **travel radius** to find alumni nearby. **Include virtual speakers** also keeps people who can join online. |
| **Strict match** | Shows only people who have *every* tag you picked. When it's off, people matching the most tags come first. |
| **Sort** | Most matches, nearest, or name A to Z. |
| **Result cards** | Each card shows what the person speaks about, their skills, and any of your picked tags they're **missing**. |
| **View profile** | Full details: role, location, languages, their Technovation project, and what they've done since. |
| **Compare** | Pick two speakers and view them side by side. |
| **Shortlist** | Save people while you search. The shortlist is kept only in the open tab and is cleared when you close it, so no contact details are saved on your computer. |

### Things to try (about 15 minutes)

Please try **each of these tasks**, then **2–3 searches you'd actually need** for a real event.

1. **Topic + audience.** Under Filters, pick **Healthcare** (Topic) and **Students** (Audience).
   *You should see people who work in health first, such as Lerato Mokoena.*
2. **A panel near an event.** Type a city (for example *San Francisco, CA, USA*) in
   **Location and travel**, set a radius, and pick **Speak as a panelist**
   (Speaking experience). Try it with **Include virtual speakers** on and off.
3. **Language.** Find someone who can speak to a **Spanish**-speaking audience.
4. **Keyword.** Type a word you'd expect in someone's story, like *robotics* or *scholarship*.
5. **Narrow down.** Pick 3–4 tags, then turn on **Strict match**. Did the list get too short?
6. **Decide.** Open two profiles, **Compare** them, and add your favorite to the **Shortlist**.

While you test, notice:
- Did the people at the top seem like good choices? Was it clear *why* each one matched?
- Was anything confusing, slow, or missing? Which filter did you look for and not find?
- How does this compare to how you find speakers today?

Then please fill out the **feedback form**: **[feedback form link]**

---

## Option B: Run it on your own computer (optional, for technical testers)

This runs the same website on your laptop. You only need it if the team asks you to test
changes before they go live. It needs **Python 3**, **git**, and the project's **Supabase
secret key**, which a team member gives you through a secure channel (never by email or chat).
If you don't have the key, use Option A.

1. Open **Terminal** (Mac) and download the project:
   ```bash
   git clone https://github.com/kingaryaprince/technovation-tagger.git
   cd technovation-tagger/search-app
   ```
   If you already have it, update it instead:
   ```bash
   cd technovation-tagger && git pull && cd search-app
   ```
2. Set the three settings for this Terminal window. Replace the parts in `<...>`, and keep the quotes:
   ```bash
   export STAFF_PASSWORD='<pick any password for your local copy>'
   export SUPABASE_URL='<the project URL from the team>'
   export SUPABASE_SERVICE_KEY='<the secret key from the team>'
   ```
   These last only until you close the window, so nothing is saved to a file.
3. Start the site:
   ```bash
   python3 server.py
   ```
   No extra packages are needed. Leave this window open while you test.
4. In your browser, go to **http://localhost:8000** and sign in with the password from step 2.
   Then follow **Things to try** above.
5. To stop, click the Terminal window and press **Control + C**.

### If something goes wrong

| What you see | What to do |
| --- | --- |
| `command not found: python3` | Install Python 3 from python.org, or run `xcode-select --install` on a Mac. |
| `Address already in use` | Another copy is running. Close it, or start on another port: `PORT=8001 python3 server.py`, then open http://localhost:8001. |
| Sign-in works but searches show an error | The Supabase URL or key is wrong or has a typo. Set them again (step 2) and restart. |
| The travel radius finds no one | The place wasn't found. Try a fuller name, like *Austin, TX, USA*. |
| `certificate verify failed` | On a Mac, run `/Applications/Python\ 3*/Install\ Certificates.command` once, then try again. |

---

## Questions or problems

Contact the student team: Pragya Thakur and Kayden Walton (admin pilot).
Please describe what you clicked and what you saw, and include a screenshot if you can.
