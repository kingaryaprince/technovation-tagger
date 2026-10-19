// Copy to app/api/admin/speakers/search/route.ts in the Next.js admin app.
// Calls ranked_speakers_search from sql/005_speaker_search.sql.
//
// Admin-only endpoint the Speaker Finder UI calls.
//
//   POST /api/admin/speakers/search
//   Authorization: Bearer <the signed-in admin's Supabase access token>
//   { "tags": [{ "category": "audiences", "tag": "Students" }, ...],
//     "limit": 5,              // optional, default 5
//     "speakersOnly": true }   // optional, default true
//
// Returns { results: [...] }, best match first. Each result has rank, response_id,
// full_name, email, match_count, total_selected, match_percent, matched_tags,
// missing_tags, availability and profile.
//
// Needs these environment variables (server only, never NEXT_PUBLIC_):
//   SUPABASE_URL=https://oyldvofbbdhqepdnnylt.supabase.co
//   SUPABASE_SECRET_KEY=sb_secret_...
//
// npm install @supabase/supabase-js

import { createClient } from "@supabase/supabase-js";
import { NextResponse } from "next/server";

type TagPick = { category: string; tag: string };

const supabaseAdmin = createClient(
  process.env.SUPABASE_URL!,
  process.env.SUPABASE_SECRET_KEY!,
  { auth: { persistSession: false } }
);

// Only staff may search. Fails closed: if anything is missing, the answer is no.
// ADJUST: change "staff" / "user_id" if the staff table in schema.sql uses other names.
async function isStaff(req: Request): Promise<boolean> {
  const token = req.headers.get("authorization")?.replace(/^Bearer\s+/i, "");
  if (!token) return false;

  const { data: userData, error: userError } = await supabaseAdmin.auth.getUser(token);
  if (userError || !userData?.user) return false;

  const { data: staffRow, error: staffError } = await supabaseAdmin
    .from("staff")
    .select("user_id")
    .eq("user_id", userData.user.id)
    .maybeSingle();

  return !staffError && staffRow !== null;
}

function parseTags(value: unknown): TagPick[] | null {
  if (!Array.isArray(value) || value.length === 0 || value.length > 50) return null;
  const tags: TagPick[] = [];
  for (const item of value) {
    if (
      typeof item !== "object" || item === null ||
      typeof (item as TagPick).category !== "string" ||
      typeof (item as TagPick).tag !== "string"
    ) {
      return null;
    }
    tags.push({ category: (item as TagPick).category, tag: (item as TagPick).tag });
  }
  return tags;
}

export async function POST(req: Request) {
  if (!(await isStaff(req))) {
    return NextResponse.json({ error: "Admins only." }, { status: 403 });
  }

  let body: { tags?: unknown; limit?: unknown; speakersOnly?: unknown };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "Body must be JSON." }, { status: 400 });
  }

  const tags = parseTags(body.tags);
  if (!tags) {
    return NextResponse.json(
      { error: 'Send "tags" as a list of 1-50 {category, tag} objects.' },
      { status: 400 }
    );
  }

  const limit = typeof body.limit === "number" ? body.limit : 5;
  const speakersOnly = typeof body.speakersOnly === "boolean" ? body.speakersOnly : true;

  const { data, error } = await supabaseAdmin.rpc("ranked_speakers_search", {
    p_tags: tags,
    p_limit: limit,
    p_speakers_only: speakersOnly,
  });

  if (error) {
    // "Unknown tag(s): ..." and "Pick at least one tag" come back from the database
    const isInputProblem = /Unknown tag|Pick at least one tag/.test(error.message);
    return NextResponse.json(
      { error: isInputProblem ? error.message : "Search failed." },
      { status: isInputProblem ? 400 : 500 }
    );
  }

  return NextResponse.json({ results: data ?? [] });
}
