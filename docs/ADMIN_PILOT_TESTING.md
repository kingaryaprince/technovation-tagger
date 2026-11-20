# Speaker Bureau pilot: testing guide for admins

Thank you for helping test the Technovation Alumni Speaker Bureau. This tool helps staff find
alumni to speak at events, sit on panels, or share their stories. It has two pages:

- **Speaker Bureau**: finds alumni who said they're open to speaking, ranked by how well they fit your event.
- **All Alumni**: a keyword search across *every* alum who filled out the intake form, whether or
  not they want to speak (for example, to find a mentor or someone to feature in a story).

Testing takes about **25 minutes**.

Once you're done, please fill out the feedback form: **[Speaker Bureau pilot feedback form](https://docs.google.com/forms/d/e/1FAIpQLSdk93mOd7htTrDB7XnIyCn3WoN1GeQ748NPdPPgaQtDCT-ICw/viewform)**

> **About the profiles:** During the pilot, most profiles in the search are **fictional sample
> alumni** that the student team wrote for testing. They have names, stories and answers that
> look real, but none of them are real people. Real alumni will
> appear as they complete the intake form, and after the pilot we will delete all of the fictional
> profiles.

---

## Option A: Use the website (recommended)

You only need a web browser.

1. Open **https://technovation-alumni-search-bureau.onrender.com/**
   - The first visit can take **up to a minute** to load while the server wakes up. That's normal.
2. Enter the **staff password**: technovation_search
3. You'll stay signed in for 12 hours. Click **Sign out** at the top when you're done.

Five wrong passwords in 15 minutes lock sign-in for 15 minutes.

### What's on the Speaker Bureau page

Switch between the two pages with **Speaker Bureau** and **All Alumni** at the top right.

| Part | What it does |
| --- | --- |
| **Search box** | Type a topic (like "Healthcare") and pick it from the suggestions to add it as a filter. You can also type a name or any word from a bio, which narrows the results of the filters you've picked. |
| **Filters** (left side) | Click tags in five groups: **Topic**, **Audience**, **Language**, **Speaking experience** and **Skills**. Only the most common tags are listed; find others by typing in the search box. Each tag a person has adds a point, and people with the most points come first. |
| **Location and travel** | Enter the event's city, press Enter, and set a **travel radius**. Living within the radius counts as one extra point. Turn **Include virtual speakers** off to list only people inside the radius. |
| **Strict match** | Shows only people who have *every* tag you picked. When it's off, people matching the most tags come first. |
| **Sort** | Most matches, nearest, or name A to Z. |
| **Result cards** | Each card shows what the person speaks about, their skills, audiences, languages and speaking types, highlights the tags you picked, and says how many of your filters they match (for example, "2 of 2 filters matched"). |
| **View profile** | Full details: role, location, languages, their Technovation project, and what they've done since. |
| **Compare** | Pick two speakers and view them side by side. |
| **Shortlist** | Save people while you search. The shortlist is kept only in the open tab and is cleared when you close it, so no contact details are saved on your computer. |

### What's on the All Alumni page

| Part | What it does |
| --- | --- |
| **Keyword search** | Type words and press Enter. Each word can match a name, role, place, project, answer or tag. More words find more people; put a phrase in quotes to match it exactly, such as `"women in stem"`. |
| **Try** buttons | Example searches: *python*, *nonprofit education* and *"women in stem"*. |
| **The list** | Everyone, A to Z, with role, location, Technovation year and email. With no search, it shows all alumni. |
| **Show profile** | Opens the full profile in place, including whether they're **willing to speak**, their availability, topics, skills and languages. |
| **Shortlist** | Works like the Speaker Bureau shortlist. |

### Things to try (about 15 minutes)

Please try **each of these tasks**, then **2–3 searches you'd actually need** for a real event.

1. **Topic + audience.** Type **Healthcare** in the search box and pick it from the suggestions.
   Then click **Students** under Audience.
   *People who work in health, such as Jasmine Park, Lerato Mokoena and Rebecca Castillo, should
   come first with "2 of 2 filters matched".*
2. **A panel near an event.** Click **Clear all** on the filters tab on the left side. Type *New York, NY, USA* in **Location and
   travel** and press Enter, then click **Speak as a panelist** (Speaking experience). Turn
   **Include virtual speakers** off to see only people within the radius, then try a larger radius.
3. **Language.** Click **Clear all**, then find someone who can speak to a **Spanish**-speaking audience.
4. **Keyword.** Click **Clear all** and pick **Students**. Then type a word you'd expect in someone's
   story, like *robotics* or *scholarship*, and press Enter. The keyword narrows the list to people
   whose profiles mention it. (A keyword on its own, with no filter picked, shows no one.)
5. **Narrow down.** Pick 3–4 tags (for example Students, Speak as a panelist, Python and Women in
   STEM), then turn on **Strict match**. Did the list get too short?
6. **Decide.** Open two profiles, **Compare** them, and add your favorite to the **Shortlist**.
7. **All Alumni.** Click **All Alumni** at the top right. Click the **python** example, then
   search *German*. *Dilnoza Karimova should appear for German, even though she doesn't show up
   for German on the Speaker Bureau page, because she isn't open to speaking.* Click **Show
   profile** on someone to see their full answers.

While you test, notice:
- Did the people at the top seem like good choices? Was it clear *why* each one matched?
- Was anything confusing, slow, or missing? Which filter did you look for and not find?
- When would you use **All Alumni** instead of the Speaker Bureau?
- How does this compare to how you find speakers today?

Then please fill out the **feedback form**: **[Speaker Bureau pilot feedback form](https://docs.google.com/forms/d/e/1FAIpQLSdk93mOd7htTrDB7XnIyCn3WoN1GeQ748NPdPPgaQtDCT-ICw/viewform)**

### Known pilot quirks (not bugs)

- **The first visit is slow.** The site sleeps when no one has used it for a while and takes up to a minute to wake up.
- **Most profiles are fictional samples** (see the note at the top).
- **A keyword needs a filter.** Typing a word with no filter or location picked shows no one. Pick a tag first, then add the keyword.
- **A few Speaker Bureau filter tags return no speakers.** The filter list includes every alum who filled out the form, but the Speaker Bureau only shows people who said they're open to speaking. A tag like **German** can belong only to someone who isn't a speaker, so it finds no one there. Search **All Alumni** to find them.
- **The shortlist is cleared when you close the tab.** Copy anything you want to keep first.

If anything else looks wrong, reply to the email this guide came with and include a screenshot.


## Questions or problems

Contact the student team. 
Please describe what you clicked and what you saw, and include a screenshot if you can.
