def call_flops(spec, n_in, n_cached, n_out):
    p = float(spec.get("active_params_b", 0)) * 1e9
    L = int(spec.get("layers", 0))
    a = int(spec.get("attn_dim", 0))
    new = max(0, n_in - n_cached)
    lin = 2 * p * (new + n_out)
    att_prefill = 4 * L * a * (new * n_cached + new * new / 2)
    att_decode = 4 * L * a * (n_out * n_in + n_out * n_out / 2)
    return lin + att_prefill + att_decode


class Meter:
    def __init__(self):
        self.t = {"tok_in": 0, "tok_out": 0, "tok_cached": 0, "tok_processed": 0, "flops": 0.0, "req_s": 0.0, "calls": 0, "retries": 0}

    def add(self, spec, u):
        f = call_flops(spec, u["in"], u.get("cached", 0), u["out"])
        self.t["tok_in"] += u["in"]
        self.t["tok_out"] += u["out"]
        self.t["tok_cached"] += u.get("cached", 0)
        self.t["tok_processed"] += max(0, u["in"] - u.get("cached", 0)) + u["out"]
        self.t["flops"] += f
        self.t["req_s"] += u.get("req_s", 0.0)
        self.t["calls"] += 1
        self.t["retries"] += u.get("retries", 0)
        return f

    def snap(self):
        d = dict(self.t)
        d["tok_total"] = d["tok_in"] + d["tok_out"]
        return d
