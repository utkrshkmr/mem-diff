import gzip
import json
import os

import numpy as np
import pandas as pd

from .analyze import md
from .timeaxis import load_timelines, main_slice, MODE_ORDER

MATCH = ["task", "backend", "seed", "model_A", "model_B", "acl", "schedule"]
CAUSES = ["correct", "never_available", "removed", "conflict_at_end", "available_not_used"]


def modes_in(df):
    return [m for m in MODE_ORDER if m in set(df["mode"])]


_DET = {}


def details(run_dir, i):
    k = (run_dir, i)
    if k not in _DET:
        p = os.path.join(run_dir, "details", i + ".json.gz")
        _DET[k] = json.load(gzip.open(p, "rt")) if os.path.exists(p) else {}
    return _DET[k]


def load_nested(run_dir, ok, key):
    rows = []
    cols = [c for c in ("id", "task", "mode", "backend", "acl", "schedule", "seed", "model_A", "model_B") if c in ok]
    for r in ok[cols].to_dict("records"):
        for x in details(run_dir, r["id"]).get(key) or []:
            rows.append({**r, **x})
    return pd.DataFrame(rows)


def step_at(df, x, ycols, grid):
    out = []
    for i, g in df.sort_values(x).groupby("id", sort=False):
        xs = g[x].values
        idx = np.searchsorted(xs, grid, side="right") - 1
        idx = np.clip(idx, 0, len(xs) - 1)
        rec = {"id": i}
        for c in ycols:
            rec[c] = g[c].values[idx]
        out.append(rec)
    return out


def curves_by_tokens(tl, ok, grid):
    rows = []
    meta = ok.set_index("id")[["n_items"]]
    for (b, m), g in tl.groupby(["backend", "mode"]):
        g = g.join(meta, on="id")
        st = step_at(g, "cum_tok_total", ["a_correct", "ev_items_avail_A", "n_items", "records", "contested", "ev_links_present", "ev_links_conflict"], grid)
        if not st:
            continue
        for k, x in enumerate(grid):
            n = np.array([s["n_items"][k] for s in st], dtype=float)
            rows.append({"backend": b, "mode": m, "tokens": x, "episodes": len(st),
                         "frac_correct_A": float(np.mean([s["a_correct"][k] for s in st] / n)),
                         "frac_evidence_A": float(np.mean([s["ev_items_avail_A"][k] for s in st] / n)),
                         "records": float(np.mean([s["records"][k] for s in st])),
                         "contested": float(np.mean([s["contested"][k] for s in st])),
                         "links_present": float(np.mean([s["ev_links_present"][k] for s in st])),
                         "links_conflict": float(np.mean([s["ev_links_conflict"][k] for s in st]))})
    return pd.DataFrame(rows)


def completion_curves(o, col, grid):
    rows = []
    for (b, m), g in o.groupby(["backend", "mode"]):
        v = g[col].astype(float).fillna(np.inf).values
        keep = g["cost_still_correct_at_end"].fillna(False).astype(bool).values if "cost_still_correct_at_end" in g else np.zeros(len(g), bool)
        for x in grid:
            rows.append({"backend": b, "mode": m, "budget": x, "episodes": len(v), "frac_completed": float(np.mean(v <= x)), "frac_completed_and_kept": float(np.mean((v <= x) & keep))})
    return pd.DataFrame(rows)


def evidence_section(run_dir, ok, out):
    rep = ["## Evidence chains (fact-finding)\n"]
    ff = ok[ok["task"] == "factfind"]
    if not len(ff):
        rep.append("_no fact-finding episodes_\n")
        return rep + completion_section(ok, out)
    items = load_nested(run_dir, ff, "evidence_items")
    links = load_nested(run_dir, ff, "links")
    m = main_slice(ff)
    mids = set(m["id"])
    if len(items):
        items.to_csv(os.path.join(out, "evidence_items.csv"), index=False)
        it = items[items["id"].isin(mids)]
        c = it.groupby(["backend", "mode"])["cause"].value_counts(normalize=True).unstack(fill_value=0)
        c = c[[x for x in CAUSES if x in c.columns]]
        c.to_csv(os.path.join(out, "evidence_causes.csv"))
        rep.append("Why each question ended correct or wrong (share of questions; main slice). never_available: the full chain was never in A's clues plus memory. removed: it was available and later lost. conflict_at_end: available but a contradicting link was still in memory. available_not_used: available and uncontested, still wrong.\n")
        rep.append(md(c, "{:.2f}"))
        lag = it.groupby(["backend", "mode"]).agg(first_avail_round=("first_avail_round", "mean"), first_correct_round=("first_correct_round", "mean"), lag_rounds=("lag_rounds", "mean"), lag_tok=("lag_tok", "mean"), first_avail_tok=("first_avail_tok", "mean"), first_correct_tok=("first_correct_tok", "mean"))
        lag.to_csv(os.path.join(out, "evidence_lag.csv"))
        rep.append("From evidence available to A answering correctly (means over questions where both happened):\n")
        rep.append(md(lag, "{:.1f}"))
    if len(links):
        links.to_csv(os.path.join(out, "evidence_links.csv"), index=False)
        lk = links[links["id"].isin(mids)]
        need = lk[~lk["in_A"].astype(bool)]
        d = need.groupby(["backend", "mode"]).agg(links=("item", "size"), ever_present=("first_present_round", lambda s: s.notna().mean()), first_present_round=("first_present_round", "mean"), supplied_by_B=("first_supplier", lambda s: (s == "B").mean()), removed=("removed", "mean"), ever_conflict=("ever_conflict", "mean"), conflict_end=("conflict_end", "mean"), conflict_rounds=("conflict_rounds", "mean"), conflict_tok=("conflict_tok", "mean"), changes=("n_changes", "mean"))
        d.to_csv(os.path.join(out, "evidence_link_dynamics.csv"))
        rep.append("Links A did not hold privately (the ones that must come through memory):\n")
        rep.append(md(d, "{:.2f}"))
    tl = load_timelines(run_dir, ff)
    if tl is not None and "ev_items_avail_A" in tl:
        tl = tl[tl["id"].isin(mids)]
        fin = tl.groupby("id")["cum_tok_total"].max()
        grid = np.linspace(0, float(np.nanpercentile(fin, 95)) if len(fin) else 1, 60)
        cv = curves_by_tokens(tl, ff, grid)
        cv.to_csv(os.path.join(out, "evidence_vs_correct_by_tokens.csv"), index=False)
        try:
            plot_ev(cv, "tokens", "cumulative tokens (both agents)", os.path.join(out, "evidence_vs_correct_by_tokens.png"))
            plot_ev(cv, "tokens", "cumulative tokens (both agents)", os.path.join(out, "evidence_vs_correct_by_tokens_focus.png"), FOCUS)
            plot_struct_tokens(cv, os.path.join(out, "structure_by_tokens.png"))
        except Exception as e:
            rep.append(f"plot error: {e}\n")
    tr = load_nested(run_dir, ff, "trajectory")
    if len(tr) and "ev_items_avail_A" in tr:
        tr = tr[tr["id"].isin(mids)].join(ff.set_index("id")["n_items"], on="id")
        tr["frac_correct_A"] = tr["a_correct"] / tr["n_items"]
        tr["frac_evidence_A"] = tr["ev_items_avail_A"] / tr["n_items"]
        R = int(tr["round"].max()) + 1
        filled = []
        for i, g in tr.groupby("id"):
            g = g.set_index("round").reindex(range(R)).ffill()
            g["id"] = i
            filled.append(g.reset_index())
        tf = pd.concat(filled)
        cr = tf.groupby(["backend", "mode", "round"])[["frac_correct_A", "frac_evidence_A", "records", "contested", "ev_links_present", "ev_links_conflict", "cum_tok_total"]].mean().reset_index()
        cr.to_csv(os.path.join(out, "evidence_vs_correct_by_round.csv"), index=False)
        try:
            plot_ev(cr.assign(round=cr["round"] + 1), "round", "round", os.path.join(out, "evidence_vs_correct_by_round.png"))
            plot_ev(cr.assign(round=cr["round"] + 1), "round", "round", os.path.join(out, "evidence_vs_correct_by_round_focus.png"), FOCUS)
        except Exception as e:
            rep.append(f"plot error: {e}\n")
    rep.append("Figures: `evidence_vs_correct_by_tokens.png` and `_focus.png` with collab/assist/sabotage/zerosum/poison only (solid: share of questions A answers correctly; dashed: share whose full evidence chain is in A's clues plus current memory), `evidence_vs_correct_by_round.png`, `structure_by_tokens.png` (memory records and chain links present / conflicted at equal token spend).\n")
    return rep + completion_section(ok, out) + matched_section(ok, out)


def completion_section(ok, out):
    rep = ["## Completion by budget\n", "Finish line: the first point when all of A's current answers are correct (team answers in collab and zerosum). Every job counts in the denominator; jobs that never finish stay at zero. 'kept' also requires the answers to still be correct at the end.\n"]
    m = main_slice(ok)
    if "cost_tok_to_complete" not in m:
        return rep
    allrows = []
    for task, g in m.groupby("task"):
        mx = float(np.nanpercentile(g["cost_tok_total"], 99)) if g["cost_tok_total"].notna().any() else 1
        tg = np.unique(np.concatenate([[0], np.geomspace(max(1, mx / 300), mx, 60)]))
        ct = completion_curves(g, "cost_tok_to_complete", tg).assign(task=task, axis="tokens")
        fx = float(np.nanpercentile(g["cost_flops"], 99)) if g["cost_flops"].notna().any() else 1
        fg = np.unique(np.concatenate([[0], np.geomspace(max(1, fx / 300), fx, 60)]))
        cf = completion_curves(g, "cost_flops_to_complete", fg).assign(task=task, axis="flops")
        allrows += [ct, cf]
        try:
            plot_completion(ct, f"{task}: jobs completed by token budget", "tokens (both agents)", os.path.join(out, f"completion_by_tokens_{task}.png"))
            plot_completion(cf, f"{task}: jobs completed by FLOP budget", "estimated FLOPs", os.path.join(out, f"completion_by_flops_{task}.png"))
        except Exception as e:
            rep.append(f"plot error: {e}\n")
    if allrows:
        pd.concat(allrows).to_csv(os.path.join(out, "completion_by_budget.csv"), index=False)
    t = m.groupby(["task", "backend", "mode"]).agg(jobs=("id", "size"), completed=("cost_completed", "mean"), kept=("cost_still_correct_at_end", "mean"), median_tok_to_complete=("cost_tok_to_complete", "median"), median_tflops_to_complete=("cost_flops_to_complete", lambda s: s.median() / 1e12))
    t.to_csv(os.path.join(out, "completion_summary.csv"))
    rep.append(md(t, "{:.2f}"))
    rep.append("Figures: `completion_by_tokens_<task>.png`, `completion_by_flops_<task>.png` (solid: completed; dashed: completed and kept).\n")
    return rep


def matched_section(ok, out):
    rep = ["## Matched pair: assist vs sabotage\n", "Same job, starting memory, clues, tools, model pairing, permissions and schedule; only B's goal differs. Deltas are sabotage minus assist on the same instance.\n"]
    a = ok[ok["mode"] == "assist"].set_index(MATCH)
    s = ok[ok["mode"] == "sabotage"].set_index(MATCH)
    j = a.join(s, lsuffix="_as", rsuffix="_sa", how="inner")
    if not len(j):
        rep.append("_no matched pairs yet_\n")
        return rep
    cols = ["score", "score_split", "score_self_A", "cost_completed", "cost_still_correct_at_end", "cost_tok_to_complete", "cost_tok_total", "cost_flops", "cost_b_tok_share", "mem_records", "mem_records_B", "mem_destructive", "mem_cross", "mem_contested", "mem_state_bytes", "mem_false_record_share", "B_writes", "B_reads", "A_reads", "A_writes", "rounds_run"]
    d = {}
    for c in cols:
        if c + "_as" in j and c + "_sa" in j:
            d[c] = pd.to_numeric(j[c + "_sa"], errors="coerce").astype(float) - pd.to_numeric(j[c + "_as"], errors="coerce").astype(float)
    D = pd.DataFrame(d).reset_index()
    D.to_csv(os.path.join(out, "matched_assist_vs_sabotage.csv"), index=False)
    t = D.groupby(["task", "backend"])[list(d)].mean()
    t.insert(0, "pairs", D.groupby(["task", "backend"]).size())
    rep.append(md(t[[c for c in ["pairs", "score", "score_self_A", "cost_completed", "cost_tok_to_complete", "cost_tok_total", "mem_records", "mem_destructive", "mem_cross", "mem_contested", "mem_false_record_share"] if c in t]], "{:+.2f}"))
    lv = ok[ok["mode"].isin(["assist", "sabotage"]) & (ok["task"] == "factfind")]
    if len(lv) and "evidence_items_avail_end" in lv:
        e = lv.groupby(["backend", "mode"])[[c for c in lv.columns if c.startswith("evidence_") and pd.api.types.is_numeric_dtype(lv[c])]].mean()
        rep.append("Evidence outcomes (fact-finding):\n")
        rep.append(md(e, "{:.2f}"))
    return rep


def _grid(n):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cols = 4
    rows = int(np.ceil(n / cols))
    fig, axs = plt.subplots(rows, cols, figsize=(4.4 * cols, 3.3 * rows), squeeze=False)
    return plt, fig, axs.flatten()


FOCUS = ["collab", "assist", "sabotage", "zerosum", "poison"]


def plot_ev(cv, x, xl, path, only=None):
    import matplotlib
    bs = sorted(cv["backend"].unique())
    plt, fig, axs = _grid(len(bs))
    colors = matplotlib.colormaps["tab10"]
    ms = [m for m in modes_in(cv) if not only or m in only]
    for ax, b in zip(axs, bs):
        g = cv[cv["backend"] == b]
        for k, mo in enumerate(ms):
            h = g[g["mode"] == mo].sort_values(x)
            if not len(h):
                continue
            ax.plot(h[x], h["frac_correct_A"], color=colors(k), lw=1.6, label=mo)
            ax.plot(h[x], h["frac_evidence_A"], color=colors(k), lw=1.1, ls="--")
        ax.set_ylim(-0.02, 1.02)
        ax.set_title(b, fontsize=10)
        ax.set_xlabel(xl, fontsize=8)
        ax.tick_params(labelsize=7)
    for ax in axs[len(bs):]:
        ax.axis("off")
    axs[0].set_ylabel("share of questions", fontsize=8)
    axs[0].legend(fontsize=7, title="solid: A correct\ndashed: evidence available", title_fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def plot_struct_tokens(cv, path):
    import matplotlib
    bs = sorted(cv["backend"].unique())
    plt, fig, axs = _grid(len(bs))
    colors = matplotlib.colormaps["tab10"]
    for ax, b in zip(axs, bs):
        g = cv[cv["backend"] == b]
        ax2 = ax.twinx()
        for k, mo in enumerate(modes_in(g)):
            h = g[g["mode"] == mo].sort_values("tokens")
            ax.plot(h["tokens"], h["records"], color=colors(k), lw=1.5, label=mo)
            ax2.plot(h["tokens"], h["links_conflict"], color=colors(k), lw=1, ls=":")
        ax.set_title(b, fontsize=10)
        ax.set_xlabel("cumulative tokens", fontsize=8)
        ax.set_ylabel("memory records (solid)", fontsize=8)
        ax2.set_ylabel("conflicted chain links (dotted)", fontsize=8)
        ax.tick_params(labelsize=7)
        ax2.tick_params(labelsize=7)
    for ax in axs[len(bs):]:
        ax.axis("off")
    axs[0].legend(fontsize=7)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def plot_completion(c, title, xl, path):
    import matplotlib
    bs = sorted(c["backend"].unique())
    plt, fig, axs = _grid(len(bs))
    colors = matplotlib.colormaps["tab10"]
    for ax, b in zip(axs, bs):
        g = c[c["backend"] == b]
        for k, mo in enumerate(modes_in(g)):
            h = g[g["mode"] == mo].sort_values("budget")
            h = h[h["budget"] > 0]
            ax.plot(h["budget"], h["frac_completed"], color=colors(k), lw=1.6, label=mo, drawstyle="steps-post")
            ax.plot(h["budget"], h["frac_completed_and_kept"], color=colors(k), lw=1, ls="--", drawstyle="steps-post")
        ax.set_xscale("log")
        ax.set_ylim(-0.02, 1.02)
        ax.set_title(b, fontsize=10)
        ax.set_xlabel(xl, fontsize=8)
        ax.tick_params(labelsize=7)
    for ax in axs[len(bs):]:
        ax.axis("off")
    axs[0].set_ylabel("fraction of jobs completed", fontsize=8)
    axs[0].legend(fontsize=7)
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
