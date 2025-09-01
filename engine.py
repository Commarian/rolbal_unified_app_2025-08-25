# engine.py
from dataclasses import dataclass
from typing import Dict, List, Tuple, Optional, Set 
import itertools
from collections import defaultdict

@dataclass
class PlayerStanding:
    player_id: int
    name: str
    section: str
    verskil: int = 0
    punte: int = 0
    bonus: int = 0

def round_result(vir: int, teen: int, rules: Dict) -> Tuple[int, int, int]:
    """Return (verskil_delta, punte_delta, bonus_delta)."""
    diff = (vir or 0) - (teen or 0)
    if diff > 0:
        pts = rules.get("POINTS_WIN", 2)
    elif diff == 0:
        pts = rules.get("POINTS_DRAW", 1)
    else:
        pts = rules.get("POINTS_LOSS", 0)
    bonus = 0
    if rules.get("BONUS_ENABLED", False):
        thr = int(rules.get("BONUS_THRESHOLD", 10))
        bpts = int(rules.get("BONUS_POINTS", 1))
        if abs(diff) >= thr:
            bonus = bpts
    return diff, pts, bonus

def sort_standings(rows: List[PlayerStanding], tiebreakers: List[str]) -> List[PlayerStanding]:
    """
    Sort by a variable-length list of tiebreakers.
    Supported keys:
      - Total, Punte, Bonus, Verskil, Player#, Coinflip, Skips draw to Jack
    Notes:
      - Coinflip: deterministic pseudo-random by player_id (stable per event).
      - Skips draw to Jack: placeholder (0 for all) unless you later attach values.
    """
    def coinflip_val(pid: int) -> int:
        # Deterministic pseudo-random number based on the id
        # (linear congruential form; ascending = "random-ish" order)
        return (pid * 9301 + 49297) % 233280

    # If no tiebreakers are provided, keep natural order by player id ascending.
    tiebreakers = list(tiebreakers or [])

    def key(r: PlayerStanding):
        parts = []
        for tb in tiebreakers:
            if tb == "Total":
                parts.append(-(r.punte + r.bonus))
            elif tb == "Punte":
                parts.append(-r.punte)
            elif tb == "Bonus":
                parts.append(-r.bonus)
            elif tb == "Verskil":
                parts.append(-r.verskil)
            elif tb == "Player#":
                parts.append(r.player_id)           # ascending
            elif tb == "Coinflip":
                parts.append(coinflip_val(r.player_id))
            elif tb == "Skips draw to Jack":
                # Placeholder: lower is better. If you later store per-player values,
                # swap the 0 for that number.
                parts.append(0)
            # Unknown tokens are ignored silently.
        # Always add a FINAL stable key to keep sort deterministic:
        parts.append(r.player_id)
        return tuple(parts)

    return sorted(rows, key=key)


def swiss_pairs(players: List[int], history: Dict[int, set]) -> List[Tuple[Optional[int], Optional[int]]]:
    """Greedy Swiss pairing avoiding repeats; odd gives last a bye (None)."""
    res = []
    used = set()
    # simple: pair neighbors while skipping repeats when possible
    for i, a in enumerate(players):
        if a in used: continue
        opponent = None
        for b in players[i+1:]:
            if b in used: continue
            if b not in history[a]:
                opponent = b
                break
        if opponent is None:
            # fall back to first available
            for b in players[i+1:]:
                if b not in used:
                    opponent = b
                    break
        if opponent is None:
            res.append((a, None))
            used.add(a)
        else:
            res.append((a, opponent))
            used.add(a); used.add(opponent)
    return res

def round_robin_pairs(players: List[int]) -> List[List[Tuple[Optional[int], Optional[int]]]]:
    """Generate full round-robin schedule (circle method)."""
    ps = players[:]
    bye = None
    if len(ps) % 2 == 1:
        ps.append(None)
    n = len(ps)
    rounds = []
    for r in range(n-1):
        half = n // 2
        pairs = []
        for i in range(half):
            a = ps[i]
            b = ps[n-1-i]
            pairs.append((a, b))
        rounds.append(pairs)
        # rotate
        ps = [ps[0]] + [ps[-1]] + ps[1:-1]
    return rounds

def rink_rotation(rinks: int, num_pairs: int, round_no: int) -> List[int]:
    """Return rink numbers for the pairs this round using cyclic rotation."""
    order = list(range(1, rinks+1))
    k = (round_no - 1) % rinks
    rotated = order[k:] + order[:k]
    return rotated[:num_pairs]

def build_history(pairings_by_round: Dict[int, List[Dict]]) -> Dict[int, set]:
    hist = defaultdict(set)
    for rnd, prs in pairings_by_round.items():
        for pr in prs:
            a = pr.get("a_id"); b = pr.get("b_id")
            if not a or not b: continue
            hist[a].add(b); hist[b].add(a)
    return hist

def compute_standings(state: Dict, section: str, rules: Dict, tiebreakers: List[str]) -> List[PlayerStanding]:
    """
    Compute standings for one section, but read pairings from ALL sections.
    Only players whose home section == `section` are credited here.
    This handles cross-section finals stored under another section's pairings key.
    """
    # init rows for this section's players only
    rows: Dict[int, PlayerStanding] = {}
    section_players = set()
    for pid_str, p in state["players"].items():
        if p["section"] != section:
            continue
        pid = int(pid_str)
        section_players.add(pid)
        rows[pid] = PlayerStanding(player_id=pid, name=p["name"], section=section)

    # aggregate across all sections, round by round
    total_rounds = int(state.get("rounds", 6))
    for r in range(1, total_rounds + 1):
        # scan ALL pairings keys that match this round (e.g., "SEKSIE 1:3", "SEKSIE 2:3")
        for key, prs in state.get("pairings", {}).items():
            try:
                sek_key, rnd_str = key.split(":")
                if int(rnd_str) != r:
                    continue
            except Exception:
                continue  # ignore malformed keys

            for pr in prs:
                a_id = pr.get("a_id"); b_id = pr.get("b_id"); rink = pr.get("rink")
                if not a_id or not b_id or not rink:
                    continue

                # scores are stored under the same section as the pairings key we are iterating
                sc = state["scores"].get(f"{sek_key}:{r}:{rink}")
                if not sc:
                    continue

                va = int(sc["a"].get("vir", 0)); ta = int(sc["a"].get("teen", 0))
                vb = int(sc["b"].get("vir", 0)); tb = int(sc["b"].get("teen", 0))

                # credit ONLY players who belong to the target `section`
                if a_id in section_players and a_id in rows:
                    dv, dp, db = round_result(va, ta, rules)
                    rows[a_id].verskil += dv; rows[a_id].punte += dp; rows[a_id].bonus += db
                if b_id in section_players and b_id in rows:
                    dv, dp, db = round_result(vb, tb, rules)
                    rows[b_id].verskil += dv; rows[b_id].punte += dp; rows[b_id].bonus += db

    return sort_standings(list(rows.values()), tiebreakers)

# ---------------- New helpers (append to engine.py) ----------------

def _preferred_rink_order(total_rinks: int) -> List[int]:
    """
    Return a preference order favoring center rinks.
    Example N=7 -> [4,3,5,2,6,1,7]. Works for any N >= 1.
    """
    if total_rinks <= 1:
        return [1] if total_rinks == 1 else []
    mid = (total_rinks + 1) // 2  # center (round up)
    order = [mid]
    left, right = mid - 1, mid + 1
    while left >= 1 or right <= total_rinks:
        if left >= 1:
            order.append(left)
            left -= 1
        if right <= total_rinks:
            order.append(right)
            right += 1
    return order

def last_rink_map(state: Dict, section: str, round_no: int) -> Dict[int, int]:
    """
    {player_id -> rink} from the immediate previous round in this section.
    Missing/first round -> empty map.
    """
    out: Dict[int, int] = {}
    if round_no <= 1:
        return out
    prev_key = f"{section}:{round_no-1}"
    prev_pairs = state.get("pairings", {}).get(prev_key, [])
    for pr in prev_pairs:
        rk = pr.get("rink")
        a_id = pr.get("a_id"); b_id = pr.get("b_id")
        if rk and a_id: out[int(a_id)] = int(rk)
        if rk and b_id: out[int(b_id)] = int(rk)
    return out

def assign_rinks_with_preferences(
    total_rinks: int,
    pairs: List[Tuple[Optional[int], Optional[int]]],
    last_rink: Dict[int, int],
) -> List[Dict]:
    """
    Greedy, deterministic assignment:
      - Iterate pairs in listed order (pass strongest-first for top rinks).
      - Prefer center rinks.
      - Avoid a rink if either team used it in the previous round.
      - Fall back to any non-forbidden; then to any available (guarantees completion).
    Returns [{rink, a_id, b_id}, ...]
    """
    available: Set[int] = set(range(1, total_rinks + 1))
    pref = _preferred_rink_order(total_rinks)
    out: List[Dict] = []
    for a, b in pairs:
        if a is None and b is None:
            continue
        forbid = set()
        if a is not None and a in last_rink: forbid.add(last_rink[a])
        if b is not None and b in last_rink: forbid.add(last_rink[b])

        chosen = None
        for r in pref:
            if r in available and r not in forbid:
                chosen = r; break
        if chosen is None:
            for r in sorted(available):
                if r not in forbid:
                    chosen = r; break
        if chosen is None and available:
            chosen = min(available)

        if chosen is None:
            out.append({"rink": 0, "a_id": a, "b_id": b})
        else:
            available.remove(chosen)
            out.append({"rink": chosen, "a_id": a, "b_id": b})
    return out

def strong_vs_strong_pairs(
    standing_rows: List[PlayerStanding],
    history: Dict[int, set]
) -> List[Tuple[Optional[int], Optional[int]]]:
    """
    Pair [1vs2, 3vs4, ...] while avoiding repeats when possible.
    Deterministic greedy: if neighbor is a repeat, pick the next best available.
    """
    ids = [r.player_id for r in standing_rows]
    used = set()
    res: List[Tuple[Optional[int], Optional[int]]] = []
    for i, a in enumerate(ids):
        if a in used: continue
        opponent = None
        for b in ids[i+1:]:
            if b in used: continue
            if b not in history.get(a, set()):
                opponent = b; break
        if opponent is None:
            for b in ids[i+1:]:
                if b not in used:
                    opponent = b; break
        if opponent is None:
            res.append((a, None))
            used.add(a)
        else:
            res.append((a, opponent))
            used.add(a); used.add(opponent)
    return res
