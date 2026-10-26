"""Conflict detection between queued/approved jobs that share targets (repos, projects, paths)."""
from difflib import SequenceMatcher

ACTIVE = ("queued", "approved", "running")
WILDCARDS = {"*", "all", "tutte", "tutte le repo", "all repos", "everything"}


def _overlap(a, b):
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def shared_targets(ta, tb):
    out = []
    for x in ta:
        for y in tb:
            if x in WILDCARDS or y in WILDCARDS:
                continue  # a wildcard says nothing precise: never raises a conflict by itself
            if _overlap(x, y):
                out.append(x if len(x) >= len(y) else y)
    return sorted(set(out))


def _covered(ta, tb):
    """Every target of `ta` is equal to, or inside, some target of `tb`."""
    return all(any(x == y or x.startswith(y + "/") for y in tb) for x in ta)


def propose(a, b, shared):
    """Simple heuristic: duplicate -> exclude; same scope+kind -> merge; subset -> integrate; else sequence."""
    ta, tb = set(a.get("targets") or []), set(b.get("targets") or [])
    sim = SequenceMatcher(None, (a.get("title") or "").lower(), (b.get("title") or "").lower()).ratio()
    if sim >= 0.8:
        return "exclude", "titoli quasi identici: tieni uno (%s), annulla l'altro" % a["id"]
    if ta == tb and (a.get("kind") or "") == (b.get("kind") or ""):
        return "merge", "stessi obiettivi e stesso tipo: fateli come un lavoro solo"
    if ta and tb and (_covered(ta, tb) or _covered(tb, ta)):
        small, big = (a, b) if _covered(ta, tb) else (b, a)
        return "integrate", "%s è contenuto in %s: integralo nel lavoro più grande" % (small["id"], big["id"])
    first = a if (a.get("status") == "approved" or b.get("status") != "approved") else b
    second = b if first is a else a
    return "sequence", "sovrapposizione parziale: prima %s, poi %s parte dal suo risultato" % (first["id"], second["id"])


def find(jobs):
    act = [j for j in jobs if j.get("status") in ACTIVE and j.get("targets")]
    out = []
    for i in range(len(act)):
        for j in range(i + 1, len(act)):
            a, b = act[i], act[j]
            sh = shared_targets(a["targets"], b["targets"])
            if sh:
                prop, why = propose(a, b, sh)
                out.append({"a": a["id"], "b": b["id"], "a_title": a["title"], "b_title": b["title"],
                            "shared": sh, "proposal": prop, "reason": why})
    return out
