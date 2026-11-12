// GET /api/league/<espnLeagueId>?season=2026
// Fetches a public ESPN fantasy league live and returns only the fields the site uses.
const json = (body, status = 200, extra = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json; charset=utf-8", ...extra } });

export async function onRequestGet({ params, request }) {
  const id = String(params.id || "");
  if (!/^\d{3,12}$/.test(id)) return json({ error: "notfound" }, 400);
  const q = new URL(request.url).searchParams.get("season");
  const now = new Date();
  const season = /^\d{4}$/.test(q || "") ? q : String(now.getUTCMonth() < 2 ? now.getUTCFullYear() - 1 : now.getUTCFullYear());
  const url = `https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/${season}/segments/0/leagues/${id}` +
    "?view=mTeam&view=mRoster&view=mMatchupScore&view=mSettings&view=mStatus";
  let r;
  try {
    r = await fetch(url, { headers: { accept: "application/json" }, cf: { cacheTtl: 60, cacheEverything: true } });
  } catch (e) {
    return json({ error: "unavailable" }, 502);
  }
  if (r.status === 401 || r.status === 403) return json({ error: "private" });
  if (r.status === 404) return json({ error: "notfound" });
  if (!r.ok) return json({ error: "unavailable" });
  let L;
  try { L = await r.json(); } catch (e) { return json({ error: "unavailable" }); }
  if (!L || !Array.isArray(L.teams)) return json({ error: "notfound" });
  const sp = L.scoringPeriodId;
  const st = L.settings || {};
  const out = {
    id: L.id,
    scoringPeriodId: sp,
    status: { currentMatchupPeriod: L.status && L.status.currentMatchupPeriod },
    settings: {
      name: st.name,
      scoringSettings: { scoringItems: ((st.scoringSettings || {}).scoringItems || []).filter(i => i.statId === 53).map(i => ({ statId: 53, points: i.points })) },
      rosterSettings: { lineupSlotCounts: (st.rosterSettings || {}).lineupSlotCounts || {} },
    },
    teams: L.teams.map(t => ({
      id: t.id, name: t.name, location: t.location, nickname: t.nickname, abbrev: t.abbrev, playoffSeed: t.playoffSeed,
      record: { overall: (t.record || {}).overall || {} },
      roster: {
        entries: ((t.roster || {}).entries || []).map(en => {
          const p = (en.playerPoolEntry || {}).player || {};
          return {
            playerId: en.playerId, lineupSlotId: en.lineupSlotId,
            playerPoolEntry: { player: {
              fullName: p.fullName, defaultPositionId: p.defaultPositionId, proTeamId: p.proTeamId, injuryStatus: p.injuryStatus,
              stats: (p.stats || []).filter(s => s.scoringPeriodId === sp && s.statSplitTypeId === 1)
                .map(s => ({ scoringPeriodId: sp, statSplitTypeId: 1, statSourceId: s.statSourceId, appliedTotal: s.appliedTotal })),
            } },
          };
        }),
      },
    })),
    schedule: (L.schedule || []).map(m => ({
      matchupPeriodId: m.matchupPeriodId, winner: m.winner,
      home: m.home && { teamId: m.home.teamId, totalPoints: m.home.totalPoints },
      away: m.away && { teamId: m.away.teamId, totalPoints: m.away.totalPoints },
    })),
  };
  return json(out, 200, { "cache-control": "public, max-age=60" });
}
