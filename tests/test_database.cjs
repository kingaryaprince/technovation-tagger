// Optional developer integration test using an ephemeral PostgreSQL engine:
// npm install --no-save @electric-sql/pglite
// node tests/test_database.cjs
// PGLITE_MODULE can point to an already installed package outside this project.
const {PGlite} = require(process.env.PGLITE_MODULE || '@electric-sql/pglite');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.join(__dirname,'..');

(async () => {
  const db = new PGlite();
  try {
    await db.exec('create role anon; create role authenticated; create role service_role bypassrls; grant usage on schema public to service_role;');
    await db.exec(fs.readFileSync(path.join(root,'sql/001_tagging.sql'),'utf8'));
    await db.exec(fs.readFileSync(path.join(root,'tests/database_smoke.sql'),'utf8'));
    let checks = 1;
    async function ingest(email, answer, response, when) {
      return (await db.query('select public.ingest_alumni_intake($1,$2,$3::jsonb,$4,$5::timestamptz) as id',
        [email,'Test',{skills:answer},response,when])).rows[0].id;
    }
    await db.exec('set role service_role');
    const first = await ingest('one@example.invalid','Python','one','2026-01-01');
    const second = await ingest('two@example.invalid','Java','two','2026-01-01');
    const claim1 = (await db.query('select * from public.claim_alumni_tagging()')).rows[0];
    const claim2 = (await db.query('select * from public.claim_alumni_tagging()')).rows[0];
    assert.notEqual(claim1.id,claim2.id); checks++;
    assert.equal((await db.query('select * from public.claim_alumni_tagging()')).rows.length,0); checks++;
    const result = {tags:{skills:['Python']}, evidence:{}, model:'test'};
    const complete = async (job, token=job.lease_token) => (await db.query(
      'select public.complete_alumni_tagging($1,$2,$3,$4::jsonb) as saved',
      [job.id,job.revision,token,result])).rows[0].saved;
    assert.equal(await complete(claim1,claim2.lease_token),false); checks++;
    await db.query("update public.alumni_intake_responses set lease_until=now()-interval '1 minute' where id=$1",[claim1.id]);
    assert.equal(await complete(claim1),false); checks++;
    const reclaimed = (await db.query('select * from public.claim_alumni_tagging()')).rows[0];
    assert.equal(reclaimed.id,claim1.id); assert.notEqual(reclaimed.lease_token,claim1.lease_token); checks++;
    assert.equal(await complete(claim1),false); checks++;
    assert.equal(await complete(reclaimed),true); checks++;
    await db.query("update public.alumni_intake_responses set lease_until=now()-interval '1 minute', attempts=3 where id=$1",[claim2.id]);
    assert.equal((await db.query('select * from public.claim_alumni_tagging()')).rows.length,0);
    assert.equal((await db.query('select tagging_status from public.alumni_intake_responses where id=$1',[claim2.id])).rows[0].tagging_status,'failed'); checks++;
    await db.exec('reset role; set role anon');
    await assert.rejects(db.query('select * from public.alumni_intake_responses'), /permission denied/); checks++;
    await assert.rejects(db.query('select * from public.claim_alumni_tagging()'), /permission denied/); checks++;
    await db.exec('reset role; set role authenticated');
    await assert.rejects(db.query('select * from public.alumni_intake_responses'), /permission denied/); checks++;
    await db.exec('reset role');
    // An empty failed result never becomes completed.
    await assert.rejects(db.query('select public.complete_alumni_tagging($1,1,$2,$3::jsonb)',
      [first,claim1.lease_token,{}]), /Invalid tagging result/); checks++;
    console.log(`Database integration passed: rollback smoke test plus ${checks-1} claim, lease, result and permission checks.`);
  } finally {await db.close();}
})().catch(error => {console.error(error.message); process.exitCode=1;});
