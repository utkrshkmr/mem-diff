import glob
import json
import os

import numpy as np
import pandas as pd

from .analyze import md, agg

GEN = ["mem_records", "mem_records_A", "mem_records_B", "mem_writes", "mem_reads", "mem_destructive", "mem_churn", "mem_cross", "mem_contested", "mem_critiques", "mem_denied", "mem_state_bytes", "mem_false_record_share"]
COST = ["cost_tok_total", "cost_tok_in", "cost_tok_out", "cost_tok_cached", "cost_tok_processed", "cost_flops", "cost_req_s", "cost_completed", "cost_tok_to_complete", "cost_flops_to_complete", "cost_t_to_complete", "cost_round_to_complete", "cost_tok_per_solved", "cost_adversary_tok_share", "wall_s"]
PAIR = ["task", "backend", "schedule", "acl", "model_A", "seed"]
MODE_ORDER = ["solo", "collab", "assist", "sabotage", "critic", "poison", "poison_warned", "zerosum"]


def main_slice(ok):
    m = ok[(ok["acl"] == "open") & (ok["schedule"] == "sequential")]
    m = m[(m["model_B"] == m["model_A"]) | (m["model_B"] == "-")]
    return m


def order(df):
    if "mode" in df.index.names:
        return df.reindex([m for m in MODE_ORDER if m in df.index.get_level_values("mode")], level="mode")
    return df


def paired(ok, cols):
    cols = [c for c in cols if c in ok.columns]
    base = ok[ok["mode"] == "collab"].groupby(PAIR)[cols].mean()
    o = ok[ok["mode"] != "collab"].join(base, on=PAIR, rsuffix="__c")
    for c in cols:
        o[c + "__d"] = o[c] - o[c + "__c"]
        o[c + "__r"] = o[c] / o[c + "__c"].replace(0, np.nan)
    return o


def structure_section(ok, out):
    rep = ["## Memory structure: same memory type, different interaction mode\n", "Main slice: open permissions, turn-by-turn commits, same model on both sides. Means per episode at the end of the episode.\n"]
    m = main_slice(ok)
    memcols = [c for c in ok.columns if c.startswith("mem_")]
    full = agg(ok, ["backend", "mode", "acl", "schedule", "model_A", "model_B"], memcols)
    full.to_csv(os.path.join(out, "structure_by_backend_mode_all.csv"))
    sm = agg(m, ["backend", "mode"], memcols)
    sm.to_csv(os.path.join(out, "structure_by_backend_mode.csv"))
    for b in sorted(m["backend"].unique()):
        g = m[m["backend"] == b]
        spec = [c for c in memcols if c not in GEN and c not in ("mem_errors", "mem_true_records", "mem_false_records") and g[c].notna().any()]
        rep.append(f"### {b}\n")
        for cols in ([c for c in GEN if c in g], spec):
            if cols:
                t = order(agg(g, ["mode"], cols))
                t.columns = [c.replace("mem_", "") for c in t.columns]
                rep.append(md(t, "{:.2f}"))
    pc = ["mem_records", "mem_records_B", "mem_destructive", "mem_cross", "mem_contested", "mem_state_bytes", "mem_false_record_share"]
    p = paired(m, pc)
    p.groupby(["backend", "mode"])[[c + "__d" for c in pc if c + "__d" in p]].mean().to_csv(os.path.join(out, "structure_delta_vs_collab.csv"))
    rep.append("### Paired change vs collaboration (same task instance, seed, model)\n")
    for c in ["mem_records", "mem_contested", "mem_destructive", "mem_cross"]:
        if c + "__d" in p:
            rep.append(f"{c.replace('mem_', '')} delta:\n")
            rep.append(md(p.pivot_table(index="backend", columns="mode", values=c + "__d", aggfunc="mean"), "{:+.2f}"))
    return rep


def cost_section(ok, out, energy):
    o = ok.copy()
    if energy:
        o["energy_wh"] = o["id"].map(energy)
    cols = [c for c in COST if c in o] + (["energy_wh"] if energy else [])
    rep = ["## Compute and tokens to complete the job\n", "tok_total counts both agents (prompt + completion). tok_processed excludes prefix-cache hits. flops is an analytic estimate (2 x active params x processed tokens + attention). *_to_complete is measured at the submission that made every item correct and is averaged over completed episodes only. energy_wh is measured GPU energy attributed to episodes by FLOP share.\n"]
    agg(o, ["backend", "mode", "acl", "schedule", "task", "model_A", "model_B"], cols).to_csv(os.path.join(out, "cost_by_backend_mode_all.csv"))
    m = main_slice(o)
    t = agg(m, ["backend", "mode"], cols)
    t.to_csv(os.path.join(out, "cost_by_backend_mode.csv"))
    show = order(t.copy())
    if "cost_flops" in show:
        show["cost_flops"] = show["cost_flops"] / 1e12
        show = show.rename(columns={"cost_flops": "tflops"})
    if "cost_flops_to_complete" in show:
        show["cost_flops_to_complete"] = show["cost_flops_to_complete"] / 1e12
        show = show.rename(columns={"cost_flops_to_complete": "tflops_to_complete"})
    show.columns = [c.replace("cost_", "") for c in show.columns]
    keep = [c for c in ["n", "tok_total", "tok_processed", "tok_cached", "tflops", "req_s", "completed", "tok_to_complete", "tflops_to_complete", "t_to_complete", "tok_per_solved", "adversary_tok_share", "energy_wh"] if c in show]
    rep.append(md(show[keep], "{:.1f}"))
    p = paired(m, ["cost_tok_total", "cost_flops"])
    if "cost_tok_total__r" in p:
        rep.append("### Paired cost ratio vs collaboration (median of per-instance tok_total ratio)\n")
        rep.append(md(p.pivot_table(index="backend", columns="mode", values="cost_tok_total__r", aggfunc="median"), "{:.2f}"))
    rep.append("### By model\n")
    bm = agg(o, ["model_A", "mode"], [c for c in ["cost_tok_total", "cost_tok_processed", "cost_flops", "cost_req_s", "cost_completed", "energy_wh"] if c in o])
    bm.to_csv(os.path.join(out, "cost_by_model_mode.csv"))
    rep.append(md(bm, "{:.3g}"))
    tot = {"episodes": len(o), "tok_total": float(o.get("cost_tok_total", pd.Series(dtype=float)).sum()), "tok_processed": float(o.get("cost_tok_processed", pd.Series(dtype=float)).sum()), "pflops": float(o.get("cost_flops", pd.Series(dtype=float)).sum()) / 1e15}
    rep.append("### Totals\n")
    rep.append("\n".join(f"- {k}: {v:,.3f}" if isinstance(v, float) else f"- {k}: {v}" for k, v in tot.items()) + "\n")
    return rep


def load_timelines(run_dir, ok):
    files = glob.glob(os.path.join(run_dir, "timelines", "*.csv.gz"))
    keep = set(ok["id"])
    parts = []
    for f in files:
        i = os.path.basename(f).split(".")[0]
        if i in keep:
            d = pd.read_csv(f)
            d["id"] = i
            parts.append(d)
    if not parts:
        return None
    tl = pd.concat(parts, ignore_index=True)
    meta = ok.set_index("id")[["task", "mode", "backend", "acl", "schedule", "model_A", "model_B", "wall_s", "t_start"]]
    tl = tl.join(meta, on="id")
    tl["tok"] = tl["tok_in"] + tl["tok_out"]
    tl["processed"] = tl["tok_in"] - tl["tok_cached"] + tl["tok_out"]
    tl["mem_ops"] = tl["mem_reads"] + tl["mem_writes"]
    return tl


def load_traj(run_dir, ok):
    from .evidence import load_nested
    return load_nested(run_dir, ok, "trajectory")


def gpu_energy(run_dir):
    p = os.path.join(run_dir, "gpu.csv")
    if not os.path.exists(p):
        return None
    g = pd.read_csv(p, on_bad_lines="skip")
    g = g.apply(pd.to_numeric, errors="coerce").dropna(subset=["unix", "power_w"])
    if len(g) < 2:
        return None
    tot = 0.0
    busy = 0.0
    for _, d in g.sort_values("unix").groupby("index"):
        dt = d["unix"].diff().clip(upper=30).fillna(0).values
        pw = d["power_w"].values
        e = (pw[1:] + pw[:-1]) / 2 * dt[1:]
        tot += e.sum()
        busy += e[(d["util_pct"].values[1:] > 5)].sum()
    return {"g": g, "joules": tot, "busy_joules": busy}


def binned(tl, by, x, xbins):
    tl = tl.copy()
    tl["xb"] = pd.cut(tl[x], xbins, labels=xbins[:-1], include_lowest=True).astype(float)
    per = tl.groupby(["id", "xb"] + by).agg(tok=("tok", "sum"), processed=("processed", "sum"), flops=("flops", "sum"), mem_ops=("mem_ops", "sum"), mem_writes=("mem_writes", "sum"), mem_read_chars=("mem_read_chars", "sum"), cum_tok=("cum_tok_total", "max"), records=("records", "max"), solved=("solved", "max")).reset_index()
    out = per.groupby(by + ["xb"]).agg(episodes=("id", "nunique"), tok=("tok", "mean"), processed=("processed", "mean"), flops=("flops", "mean"), mem_ops=("mem_ops", "mean"), mem_writes=("mem_writes", "mean"), mem_read_chars=("mem_read_chars", "mean"), cum_tok=("cum_tok", "mean"), records=("records", "mean"), solved=("solved", "mean")).reset_index()
    return out


def time_section(run_dir, ok, out):
    rep = ["## Usage and intensity over time\n"]
    tl = load_timelines(run_dir, ok)
    energy = None
    en = gpu_energy(run_dir)
    if tl is None:
        rep.append("_no timelines_\n")
        return None, rep
    run = run_level(tl, ok, en, out)
    tl = tl[(tl["acl"] == "open")]
    bin_s = 10.0
    tmax = float(np.nanpercentile(tl["t"], 99)) if len(tl) else 0
    abins = np.arange(0, max(tmax, bin_s) + bin_s, bin_s)
    ab = binned(tl, ["schedule", "backend", "mode"], "t", abins)
    for c in ["tok", "processed", "flops", "mem_ops", "mem_writes", "mem_read_chars"]:
        ab[c + "_per_s"] = ab[c] / bin_s
    ab.to_csv(os.path.join(out, "timeline_binned_seconds.csv"), index=False)
    tl["dur"] = tl.groupby("id")["t"].transform("max").replace(0, np.nan)
    tl["progress"] = tl["t"] / tl["dur"]
    nb = binned(tl, ["schedule", "backend", "mode"], "progress", np.linspace(0, 1, 21))
    nb.to_csv(os.path.join(out, "timeline_binned_progress.csv"), index=False)
    tr = load_traj(run_dir, ok)
    if len(tr):
        cols = [c for c in tr.columns if c not in ("id", "task", "mode", "backend", "acl", "schedule", "model_A", "model_B") and pd.api.types.is_numeric_dtype(tr[c])]
        tr.groupby(["schedule", "acl", "backend", "mode", "round"])[cols].mean().to_csv(os.path.join(out, "trajectory_by_round.csv"))
    rep.append("Per-call logs are in `timelines/<episode>.csv.gz`. Binned means: `timeline_binned_seconds.csv` (10 s bins), `timeline_binned_progress.csv` (fraction of episode), `trajectory_by_round.csv` (memory structure and cumulative cost at the end of each round).\n")
    seq = ab[ab["schedule"] == "sequential"]
    peak = seq.groupby(["backend", "mode"]).agg(peak_tok_per_s=("tok_per_s", "max"), mean_tok_per_s=("tok_per_s", "mean"), peak_mem_ops_per_s=("mem_ops_per_s", "max"))
    rep.append("Per-episode intensity (sequential commits), tokens/s within an episode:\n")
    rep.append(md(order(peak), "{:.1f}"))
    rep += run["rep"]
    if en is not None and "cost_flops" in ok:
        f = ok.set_index("id")["cost_flops"].fillna(0)
        if f.sum() > 0:
            energy = (f / f.sum() * en["busy_joules"] / 3600).to_dict()
    try:
        plot_time(seq, nb[nb["schedule"] == "sequential"], tr, out)
    except Exception as e:
        rep.append(f"plot error: {e}\n")
    return energy, rep


def run_level(tl, ok, en, out):
    rep = ["### Whole run on the machine\n"]
    t0 = tl["wall"].min()
    tl = tl.assign(minute=((tl["wall"] - t0) // 60).astype(int))
    r = tl.groupby("minute").agg(tok=("tok", "sum"), processed=("processed", "sum"), flops=("flops", "sum"), calls=("i", "count"), mem_ops=("mem_ops", "sum"))
    r["tok_per_s"] = r["tok"] / 60
    r["tflops_per_s"] = r["flops"] / 60 / 1e12
    st = ok["t_start"] - t0 if "t_start" in ok else None
    if st is not None:
        s0 = (st // 60).values
        s1 = ((st + ok["wall_s"]) // 60).values
        r["active_episodes"] = [int(((s0 <= m) & (s1 >= m)).sum()) for m in r.index]
    if en is not None:
        g = en["g"]
        g = g[(g["unix"] >= t0 - 60) & (g["unix"] <= tl["wall"].max() + 60)]
        g = g.assign(minute=((g["unix"] - t0) // 60).astype(int))
        pw = g.groupby(["minute", "index"])[["power_w", "util_pct", "mem_used_mib"]].mean().groupby("minute").agg(power_w=("power_w", "sum"), util_pct=("util_pct", "mean"), mem_used_mib=("mem_used_mib", "sum"))
        r = r.join(pw, how="outer")
    r.to_csv(os.path.join(out, "run_timeline_minutes.csv"))
    dur_h = (tl["wall"].max() - t0) / 3600
    lines = [f"- span of LLM activity: {dur_h:.2f} h", f"- tokens: {tl['tok'].sum():,.0f} (processed after cache {tl['processed'].sum():,.0f})", f"- estimated compute: {tl['flops'].sum() / 1e15:,.2f} PFLOP", f"- mean throughput: {tl['tok'].sum() / max(1, dur_h * 3600):,.0f} tok/s, peak minute {r['tok_per_s'].max():,.0f} tok/s"]
    if en is not None:
        lines += [f"- GPU energy (all samples): {en['joules'] / 3.6e6:,.3f} kWh; while busy: {en['busy_joules'] / 3.6e6:,.3f} kWh", f"- energy per 1k processed tokens: {en['busy_joules'] / max(1, tl['processed'].sum()) * 1000:,.1f} J"]
    rep.append("\n".join(lines) + "\n")
    try:
        plot_run(r, out)
    except Exception as e:
        rep.append(f"plot error: {e}\n")
    return {"rep": rep}


def _grid(n):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    cols = 4
    rows = int(np.ceil(n / cols))
    fig, axs = plt.subplots(rows, cols, figsize=(4.2 * cols, 3.2 * rows), squeeze=False, sharex=True)
    return plt, fig, axs.flatten()


def plot_time(ab, nb, tr, out):
    specs = [(ab, "xb", "tok_per_s", "elapsed s", "tokens/s per episode", "time_tokens_per_s.png"),
             (ab, "xb", "cum_tok", "elapsed s", "cumulative tokens", "time_cum_tokens.png"),
             (ab, "xb", "mem_ops_per_s", "elapsed s", "memory ops/s", "time_mem_ops_per_s.png"),
             (ab, "xb", "records", "elapsed s", "memory records", "time_mem_records.png"),
             (nb, "xb", "tok", "episode progress", "tokens per 5% of episode", "progress_tokens.png"),
             (nb, "xb", "solved", "episode progress", "items correct", "progress_solved.png")]
    for df, x, y, xl, yl, fn in specs:
        bs = sorted(df["backend"].unique())
        plt, fig, axs = _grid(len(bs))
        for ax, b in zip(axs, bs):
            g = df[df["backend"] == b]
            for mo in [m for m in MODE_ORDER if m in g["mode"].unique()]:
                h = g[g["mode"] == mo].sort_values(x)
                ax.plot(h[x], h[y], label=mo, lw=1.4)
            ax.set_title(b, fontsize=10)
            ax.set_xlabel(xl, fontsize=8)
            ax.set_ylabel(yl, fontsize=8)
            ax.tick_params(labelsize=7)
        for ax in axs[len(bs):]:
            ax.axis("off")
        axs[0].legend(fontsize=7)
        fig.tight_layout()
        fig.savefig(os.path.join(out, fn), dpi=120)
        plt.close(fig)
    if len(tr):
        t = tr[(tr["schedule"] == "sequential") & (tr["acl"] == "open")]
        for y, fn in (("records", "round_mem_records.png"), ("cum_tok_total", "round_cum_tokens.png"), ("contested", "round_contested.png")):
            if y not in t:
                continue
            g2 = t.groupby(["backend", "mode", "round"])[y].mean().reset_index()
            bs = sorted(g2["backend"].unique())
            plt, fig, axs = _grid(len(bs))
            for ax, b in zip(axs, bs):
                g = g2[g2["backend"] == b]
                for mo in [m for m in MODE_ORDER if m in g["mode"].unique()]:
                    h = g[g["mode"] == mo]
                    ax.plot(h["round"] + 1, h[y], marker="o", ms=3, label=mo, lw=1.4)
                ax.set_title(b, fontsize=10)
                ax.set_xlabel("round", fontsize=8)
                ax.set_ylabel(y, fontsize=8)
                ax.tick_params(labelsize=7)
            for ax in axs[len(bs):]:
                ax.axis("off")
            axs[0].legend(fontsize=7)
            fig.tight_layout()
            fig.savefig(os.path.join(out, fn), dpi=120)
            plt.close(fig)


def plot_run(r, out):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    panels = [c for c in ["tok_per_s", "tflops_per_s", "active_episodes", "power_w", "util_pct"] if c in r]
    fig, axs = plt.subplots(len(panels), 1, figsize=(11, 2.1 * len(panels)), sharex=True, squeeze=False)
    for ax, c in zip(axs[:, 0], panels):
        ax.plot(r.index / 60, r[c], lw=1.1)
        ax.set_ylabel(c, fontsize=8)
        ax.tick_params(labelsize=7)
    axs[-1, 0].set_xlabel("hours since first call")
    fig.tight_layout()
    fig.savefig(os.path.join(out, "run_timeline.png"), dpi=120)
    plt.close(fig)
