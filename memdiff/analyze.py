import json
import os

import pandas as pd

KEYS = ["task", "mode", "backend", "acl", "schedule", "model_A", "model_B"]
ADV = ["critic", "poison", "poison_warned", "zerosum"]


def load(run_dir):
    rows = {}
    for line in open(os.path.join(run_dir, "episodes.jsonl")):
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        rows[r["id"]] = r
    flat = []
    for r in rows.values():
        f = {k: v for k, v in r.items() if k not in ("agents", "cost", "mem", "trajectory")}
        for grp in ("cost", "mem"):
            for k, v in (r.get(grp) or {}).items():
                f[f"{grp}_{k}"] = v
        for a, st in (r.get("agents") or {}).items():
            for k, v in st.items():
                f[f"{a}_{k}"] = v
        flat.append(f)
    df = pd.DataFrame(flat)
    if "model_B" in df:
        df["model_B"] = df["model_B"].fillna("-")
    return df


def md(df, floatfmt="{:.3f}"):
    if df is None or len(df) == 0:
        return "_none_\n"
    df = df.reset_index()
    df.columns = [" ".join(map(str, c)).strip() if isinstance(c, tuple) else str(c) for c in df.columns]
    cols = list(df.columns)
    out = ["| " + " | ".join(map(str, cols)) + " |", "|" + "---|" * len(cols)]
    for _, r in df.iterrows():
        cells = []
        for c in cols:
            v = r[c]
            cells.append(floatfmt.format(v) if isinstance(v, float) and v == v else ("" if v is None or (isinstance(v, float) and v != v) else str(v)))
        out.append("| " + " | ".join(cells) + " |")
    return "\n".join(out) + "\n"


def agg(df, by, cols):
    cols = [c for c in cols if c in df.columns]
    g = df.groupby(by)[cols].mean(numeric_only=True)
    g.insert(0, "n", df.groupby(by).size())
    return g


def analyze(run_dir):
    df = load(run_dir)
    out = os.path.join(run_dir, "analysis")
    os.makedirs(out, exist_ok=True)
    failed = df[df["failed"].notna()] if "failed" in df else df.iloc[0:0]
    ok = df[df["failed"].isna()] if "failed" in df else df
    ok.to_csv(os.path.join(out, "episodes_flat.csv"), index=False)
    rep = [f"# Results: {os.path.basename(os.path.abspath(run_dir))}\n", f"episodes ok: {len(ok)}, failed: {len(failed)}\n"]
    if len(ok) == 0:
        open(os.path.join(out, "report.md"), "w").write("\n".join(rep))
        return
    rep.append(f"memory replay determinism: {ok['replay_ok'].mean():.4f} of episodes replay to identical state hash\n")
    main = agg(ok, KEYS, ["score", "score_split", "score_self_A", "score_self_B", "rounds_run", "A_tok_out", "B_tok_out"])
    main.to_csv(os.path.join(out, "summary_main.csv"))
    base = ok[ok["mode"] == "collab"].groupby(["task", "backend", "schedule", "model_A", "seed"])[["score", "score_split", "score_self_A"]].mean()
    base.columns = ["collab_" + c for c in base.columns]
    solo = ok[ok["mode"] == "solo"].groupby(["task", "backend", "model_A", "seed"])[["score"]].mean()
    solo.columns = ["solo_score"]
    adv = ok[ok["mode"].isin(ADV)].copy()
    if len(adv):
        adv = adv.join(base, on=["task", "backend", "schedule", "model_A", "seed"]).join(solo, on=["task", "backend", "model_A", "seed"])
        adv["delta_vs_collab"] = adv["score"] - adv["collab_score"]
        adv["delta_selfA_vs_collab"] = adv["score_self_A"] - adv["collab_score_self_A"]
        adv["delta_vs_solo"] = adv["score"] - adv["solo_score"]
        d = agg(adv, ["task", "mode", "backend", "acl", "model_A", "model_B"], ["score", "collab_score", "solo_score", "delta_vs_collab", "delta_selfA_vs_collab", "delta_vs_solo"])
        d.to_csv(os.path.join(out, "adversarial_deltas.csv"))
        rob = adv.groupby(["mode", "backend", "acl"])["delta_vs_collab"].mean().unstack(["mode"]).round(3)
        rob.to_csv(os.path.join(out, "robustness_backend_by_mode.csv"))
    rep.append("## Mean score by task x mode x backend (all models pooled)\n")
    rep.append(md(ok.pivot_table(index=["task", "backend"], columns="mode", values="score", aggfunc="mean")))
    if len(adv):
        rep.append("## Adversarial degradation vs collaborative baseline (delta_vs_collab, pooled over tasks and models)\n")
        rep.append(md(rob))
        rep.append("## Poison robustness on items the defender could solve alone (delta_selfA_vs_collab)\n")
        p = adv[adv["mode"].str.startswith("poison")]
        if len(p):
            pc = ["score", "score_self_A", "score_split", "delta_selfA_vs_collab", "adopted_poison", "wrong_A", "B_false_pairs", "B_cross_mutations", "A_critiques", "B_writes"]
            t = agg(p, ["mode", "backend", "acl"], pc)
            t.to_csv(os.path.join(out, "poison.csv"))
            rep.append(md(t))
    z = ok[ok["mode"] == "zerosum"]
    if len(z):
        rep.append("## Zero-sum dynamics\n")
        zc = ["points_A", "points_B", "items_won", "A_share_rate", "B_share_rate", "A_false_pairs", "B_false_pairs", "A_cross_mutations", "B_cross_mutations"]
        t = agg(z, ["task", "backend", "acl", "schedule"], zc)
        t.to_csv(os.path.join(out, "zerosum.csv"))
        rep.append(md(t))
        c = ok[ok["mode"] == "collab"]
        if len(c) and "A_share_rate" in c:
            rep.append("Share rate under collaboration for reference:\n")
            rep.append(md(agg(c[c["task"] == "factfind"], ["backend"], ["A_share_rate", "B_share_rate", "A_false_pairs", "B_false_pairs"])))
    cr = ok[ok["mode"] == "critic"]
    if len(cr):
        rep.append("## Critic-only partner\n")
        t = agg(cr, ["task", "backend", "acl"], ["score", "B_critiques", "B_denied", "A_writes", "A_reads", "rounds_run"])
        t.to_csv(os.path.join(out, "critic.csv"))
        rep.append(md(t))
    if ok["schedule"].nunique() > 1:
        rep.append("## Sequential vs simultaneous commits\n")
        rep.append(md(ok.pivot_table(index=["mode", "backend"], columns="schedule", values="score", aggfunc="mean")))
    if ok["acl"].nunique() > 1:
        rep.append("## Open vs owner-only permissions\n")
        rep.append(md(ok.pivot_table(index=["mode", "backend"], columns="acl", values="score", aggfunc="mean")))
    rep.append("## By model pairing\n")
    rep.append(md(ok.pivot_table(index=["model_A", "model_B"], columns="mode", values="score", aggfunc="mean")))
    rep.append("## Tool-use health\n")
    hc = ["A_no_tool_turns", "A_bad_args", "A_errors", "A_denied", "B_no_tool_turns", "B_bad_args", "B_errors", "A_tok_in", "A_tok_out", "wall_s"]
    rep.append(md(agg(ok, ["model_A"], hc), "{:.1f}"))
    if len(failed):
        rep.append("## Failures\n")
        rep.append(md(failed.groupby(["model_A", "mode"])["failed"].agg(lambda s: s.iloc[0][:120]).to_frame().join(failed.groupby(["model_A", "mode"]).size().rename("n"))))
    from .timeaxis import structure_section, cost_section, time_section
    energy = None
    try:
        energy, tsec = time_section(run_dir, ok, out)
    except Exception as e:
        tsec = [f"time-axis analysis error: {e}\n"]
    rep = rep[:3] + structure_section(ok, out) + cost_section(ok, out, energy) + tsec + rep[3:]
    open(os.path.join(out, "report.md"), "w").write("\n".join(rep))
    try:
        plots(ok, adv if len(adv) else None, out)
    except Exception as e:
        print("plot error", e)
    print(f"[analyze] wrote {out}")


def plots(ok, adv, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    for task in sorted(ok["task"].unique()):
        t = ok[ok["task"] == task].groupby(["backend", "mode"])["score"].mean().unstack()
        fig, ax = plt.subplots(figsize=(1.2 * len(t.columns) + 2.5, 0.45 * len(t) + 1.5))
        im = ax.imshow(t.values, vmin=0, vmax=1, cmap="viridis", aspect="auto")
        ax.set_xticks(range(len(t.columns)), t.columns, rotation=30, ha="right")
        ax.set_yticks(range(len(t)), t.index)
        for i in range(t.shape[0]):
            for j in range(t.shape[1]):
                v = t.values[i, j]
                if v == v:
                    ax.text(j, i, f"{v:.2f}", ha="center", va="center", color="white" if v < 0.6 else "black", fontsize=8)
        ax.set_title(f"{task}: mean score")
        fig.colorbar(im, ax=ax, fraction=0.03)
        fig.tight_layout()
        fig.savefig(os.path.join(out, f"heat_{task}.png"), dpi=130)
        plt.close(fig)
    if adv is not None and "delta_vs_collab" in adv:
        t = adv.groupby(["backend", "mode"])["delta_vs_collab"].mean().unstack()
        fig, ax = plt.subplots(figsize=(1.2 * len(t.columns) + 2.5, 0.45 * len(t) + 1.5))
        im = ax.imshow(t.values, vmin=-0.6, vmax=0.6, cmap="RdBu", aspect="auto")
        ax.set_xticks(range(len(t.columns)), t.columns, rotation=30, ha="right")
        ax.set_yticks(range(len(t)), t.index)
        for i in range(t.shape[0]):
            for j in range(t.shape[1]):
                v = t.values[i, j]
                if v == v:
                    ax.text(j, i, f"{v:+.2f}", ha="center", va="center", fontsize=8)
        ax.set_title("score change vs collaborative baseline")
        fig.colorbar(im, ax=ax, fraction=0.03)
        fig.tight_layout()
        fig.savefig(os.path.join(out, "delta_vs_collab.png"), dpi=130)
        plt.close(fig)
