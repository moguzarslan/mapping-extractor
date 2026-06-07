"""
Manual-match GUI for the requirements evaluator.

Run:
    python gui.py

Workflow
--------
1. Enter paths (GT xlsx, LLM json, output xlsx, threshold).
2. Click "Run Evaluation" — metrics, matched pairs and unmatched items appear.
3. On the "Manual Matching" tab you see everything at once:
     - the current matched pairs (tagged auto / manual),
     - the unmatched LLM extractions (False Positives),
     - the unmatched GT requirements (False Negatives).
4. To CREATE a match: select one unmatched LLM row + one unmatched GT row,
   then click "Add Match".
5. To EDIT/FIX a wrong match: select it in the matched table and click
   "Unmatch Selected Pair" — both items return to the unmatched lists, where
   you can re-pair them correctly.
6. Every change recomputes all metrics instantly.
7. Optionally save the updated xlsx with "Save Report".
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
from pathlib import Path

from service.evaluator_service import (
    load_ground_truth,
    load_llm_extraction,
    compute_similarity,
    greedy_match,
    build_report,
    norm_text,
    norm_categorical,
    FIELD_SPECS,
)
import pandas as pd


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shorten(text, max_len=120):
    if not text:
        return ""
    s = str(text)
    return s if len(s) <= max_len else s[:max_len] + "…"


# ---------------------------------------------------------------------------
# Main GUI
# ---------------------------------------------------------------------------

class EvaluatorGUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Requirements Evaluator — Manual Match")
        self.geometry("1400x900")
        self.minsize(900, 600)

        # State
        self._gt = []
        self._llm = []
        self._sim = None
        self._pairs = []      # list of (llm_idx, gt_idx, score)
        self._threshold = 0.35
        self._report = None
        self._manual_keys = set()  # {(llm_idx, gt_idx)} created/kept via the GUI

        # Index maps: unmatched_llm / unmatched_gt store original indices
        self._unmatched_llm = []   # [llm_idx, ...]
        self._unmatched_gt = []    # [gt_idx, ...]

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self):
        # ── top: file inputs ──────────────────────────────────────────
        top = ttk.LabelFrame(self, text="Inputs", padding=8)
        top.pack(fill="x", padx=8, pady=(8, 0))

        self._gt_var   = self._file_row(top, "Ground Truth xlsx:", 0)
        self._llm_var  = self._file_row(top, "LLM JSON:          ", 1)
        self._out_var  = self._file_row(top, "Output xlsx:       ", 2, save=True)
        self._thr_var  = tk.StringVar(value="0.35")

        ttk.Label(top, text="Threshold:").grid(row=3, column=0, sticky="w", pady=2)
        ttk.Entry(top, textvariable=self._thr_var, width=8).grid(row=3, column=1, sticky="w")

        btn_frame = ttk.Frame(top)
        btn_frame.grid(row=4, column=0, columnspan=3, pady=(6, 0), sticky="w")
        ttk.Button(btn_frame, text="Run Evaluation", command=self._run_eval).pack(side="left", padx=(0, 6))
        ttk.Button(btn_frame, text="Save Report",    command=self._save_report).pack(side="left")

        self._status = ttk.Label(btn_frame, text="", foreground="gray")
        self._status.pack(side="left", padx=10)

        # ── notebook: metrics | manual match ──────────────────────────
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=8)

        self._tab_metrics = ttk.Frame(nb)
        self._tab_match   = ttk.Frame(nb)
        nb.add(self._tab_metrics, text="Evaluation Metrics")
        nb.add(self._tab_match,   text="Manual Matching")

        self._build_metrics_tab()
        self._build_match_tab()

    def _file_row(self, parent, label, row, save=False):
        var = tk.StringVar()
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
        ttk.Entry(parent, textvariable=var, width=70).grid(row=row, column=1, sticky="ew", padx=4)
        cmd = (lambda v=var: self._browse_save(v)) if save else (lambda v=var: self._browse_open(v))
        ttk.Button(parent, text="Browse", command=cmd).grid(row=row, column=2)
        parent.columnconfigure(1, weight=1)
        return var

    def _browse_open(self, var):
        path = filedialog.askopenfilename()
        if path:
            var.set(path)

    def _browse_save(self, var):
        path = filedialog.asksaveasfilename(defaultextension=".xlsx",
                                            filetypes=[("Excel files", "*.xlsx")])
        if path:
            var.set(path)

    # ── Metrics tab ───────────────────────────────────────────────────

    def _build_metrics_tab(self):
        f = self._tab_metrics

        # Summary counts
        summary_frame = ttk.LabelFrame(f, text="Requirement Matching Summary", padding=6)
        summary_frame.pack(fill="x", padx=8, pady=(8, 4))

        self._summary_vars = {}
        labels = ["Ground truth count", "LLM extracted count",
                  "True Positives (matched)", "False Positives", "False Negatives"]
        for col, lbl in enumerate(labels):
            ttk.Label(summary_frame, text=lbl, font=("", 9, "bold")).grid(
                row=0, column=col, padx=12, pady=2)
            v = tk.StringVar(value="-")
            self._summary_vars[lbl] = v
            ttk.Label(summary_frame, textvariable=v, font=("", 11)).grid(
                row=1, column=col, padx=12)

        # Description metrics
        desc_frame = ttk.LabelFrame(f, text="Description Field (Requirement-Level)", padding=6)
        desc_frame.pack(fill="x", padx=8, pady=4)
        self._desc_tree = self._make_tree(
            desc_frame, ["field", "precision", "recall", "mean semantic meaning"], height=2)

        # Other fields
        other_frame = ttk.LabelFrame(f, text="Other Fields (Accuracy over matched pairs)", padding=6)
        other_frame.pack(fill="both", expand=True, padx=8, pady=(4, 8))
        self._other_tree = self._make_tree(other_frame, ["field", "accuracy"], height=7)

    # ── Manual Match tab ──────────────────────────────────────────────

    def _build_match_tab(self):
        f = self._tab_match

        # ── Top: current matched pairs (editable) ─────────────────────
        matched_frame = ttk.LabelFrame(
            f, text="Matched Pairs (TP) — select a wrong one and Unmatch to fix it",
            padding=6)
        matched_frame.pack(fill="both", expand=True, padx=8, pady=(8, 4))

        m_cols = ["source", "LLM_ID", "GT_ID", "sim", "LLM_description", "GT_description"]
        self._matched_tree = self._make_tree(matched_frame, m_cols, height=10,
                                             selectmode="browse")
        self._matched_tree.tag_configure("manual", background="#fff3cd")

        mbtn = ttk.Frame(matched_frame)
        mbtn.pack(fill="x", pady=(4, 0))
        ttk.Button(mbtn, text="Unmatch Selected Pair",
                   command=self._unmatch_selected).pack(side="left", padx=4)
        self._unmatch_status = ttk.Label(mbtn, text="", foreground="orange")
        self._unmatch_status.pack(side="left", padx=10)

        # ── Bottom: unmatched LLM | unmatched GT, side by side ────────
        panes = ttk.PanedWindow(f, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8, pady=(4, 4))

        llm_frame = ttk.LabelFrame(panes, text="Unmatched LLM Extractions (False Positives)", padding=6)
        panes.add(llm_frame, weight=1)
        fp_cols = ["LLM_ID", "LLM_type", "LLM_description", "best_similarity", "closest_GT_ID"]
        self._fp_tree = self._make_tree(llm_frame, fp_cols, height=12, selectmode="browse")

        gt_frame = ttk.LabelFrame(panes, text="Unmatched GT Requirements (False Negatives)", padding=6)
        panes.add(gt_frame, weight=1)
        fn_cols = ["GT_ID", "GT_type", "GT_description", "best_similarity", "closest_LLM_ID"]
        self._fn_tree = self._make_tree(gt_frame, fn_cols, height=12, selectmode="browse")

        # ── Action bar for creating matches ───────────────────────────
        bottom = ttk.Frame(f)
        bottom.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(bottom, text="Add Match  (one row from each list above)",
                   command=self._add_match).pack(side="left", padx=4)
        self._match_status = ttk.Label(bottom, text="", foreground="blue")
        self._match_status.pack(side="left", padx=10)

    # ------------------------------------------------------------------
    # Tree helpers
    # ------------------------------------------------------------------

    def _make_tree(self, parent, columns, height=8, selectmode="browse"):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)

        tree = ttk.Treeview(frame, columns=columns, show="headings",
                            height=height, selectmode=selectmode)
        for col in columns:
            tree.heading(col, text=col)
            w = 200 if "description" in col.lower() else 90
            tree.column(col, width=w, minwidth=50)

        vsb = ttk.Scrollbar(frame, orient="vertical",   command=tree.yview)
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return tree

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def _run_eval(self):
        gt_path  = self._gt_var.get().strip()
        llm_path = self._llm_var.get().strip()
        try:
            self._threshold = float(self._thr_var.get())
        except ValueError:
            messagebox.showerror("Invalid threshold", "Threshold must be a number.")
            return
        if not gt_path or not llm_path:
            messagebox.showerror("Missing input", "Please provide both GT and LLM paths.")
            return

        self._status.config(text="Running…", foreground="orange")
        self.update_idletasks()

        def worker():
            try:
                gt  = load_ground_truth(gt_path)
                llm = load_llm_extraction(llm_path)
                sim = compute_similarity(
                    [norm_text(r["description"]) or "" for r in gt],
                    [norm_text(r["description"]) or "" for r in llm],
                )
                type_spec = next(s for s in FIELD_SPECS if s["name"] == "type")
                llm_types = [norm_categorical(r.get(type_spec["name"])) for r in llm]
                gt_types  = [norm_categorical(r.get(type_spec["name"])) for r in gt]
                pairs = greedy_match(sim, self._threshold,
                                     llm_types=llm_types, gt_types=gt_types)
                self.after(0, lambda: self._on_eval_done(gt, llm, sim, pairs))
            except Exception as exc:
                self.after(0, lambda: self._on_eval_error(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _on_eval_done(self, gt, llm, sim, pairs):
        self._gt    = gt
        self._llm   = llm
        self._sim   = sim
        self._pairs = list(pairs)
        self._refresh_report()
        self._status.config(text="Done.", foreground="green")

    def _on_eval_error(self, exc):
        self._status.config(text=f"Error: {exc}", foreground="red")
        messagebox.showerror("Evaluation error", str(exc))

    # ------------------------------------------------------------------
    # Report refresh (called after every change to pairs)
    # ------------------------------------------------------------------

    def _refresh_report(self):
        report = build_report(self._gt, self._llm, self._sim,
                              self._pairs, self._threshold,
                              forced_pairs=self._manual_keys)
        self._report = report
        stats = report["stats"]

        # Summary
        for key, val in [
            ("Ground truth count",      len(self._gt)),
            ("LLM extracted count",     len(self._llm)),
            ("True Positives (matched)", stats["tp"]),
            ("False Positives",         stats["fp"]),
            ("False Negatives",         stats["fn"]),
        ]:
            self._summary_vars[key].set(str(val))

        # Description metrics
        self._desc_tree.delete(*self._desc_tree.get_children())
        for _, row in report["Field_Metrics_Desc"].iterrows():
            self._desc_tree.insert("", "end", values=list(row))

        # Other field metrics
        self._other_tree.delete(*self._other_tree.get_children())
        for _, row in report["Field_Metrics_Other"].iterrows():
            self._other_tree.insert("", "end", values=list(row))

        # Matched pairs (editable, on the Manual Matching tab).
        # Manual edits first, then auto pairs by descending similarity.
        # iid = "<llm_idx>_<gt_idx>" so Unmatch can recover the exact pair.
        self._matched_tree.delete(*self._matched_tree.get_children())
        sorted_pairs = sorted(
            self._pairs,
            key=lambda p: (0 if (p[0], p[1]) in self._manual_keys else 1, -p[2]),
        )
        for i, j, s in sorted_pairs:
            is_manual = (i, j) in self._manual_keys
            vals = ["manual" if is_manual else "auto",
                    self._llm[i].get("id", ""), self._gt[j].get("id", ""),
                    round(float(s), 4),
                    _shorten(self._llm[i].get("description", "")),
                    _shorten(self._gt[j].get("description", ""))]
            self._matched_tree.insert("", "end", iid=f"{i}_{j}", values=vals,
                                      tags=("manual",) if is_manual else ())

        # Rebuild index maps for manual matching
        matched_llm = {i for i, _, _ in self._pairs}
        matched_gt  = {j for _, j, _ in self._pairs}
        self._unmatched_llm = [i for i in range(len(self._llm)) if i not in matched_llm]
        self._unmatched_gt  = [j for j in range(len(self._gt))  if j not in matched_gt]

        # FP tree
        self._fp_tree.delete(*self._fp_tree.get_children())
        for i in self._unmatched_llm:
            r = self._llm[i]
            best_j = int(self._sim[i].argmax())
            vals = [
                r.get("id", ""),
                r.get("type", ""),
                _shorten(r.get("description", "")),
                round(float(self._sim[i].max()), 4),
                self._gt[best_j].get("id", ""),
            ]
            self._fp_tree.insert("", "end", iid=str(i), values=vals)

        # FN tree
        self._fn_tree.delete(*self._fn_tree.get_children())
        for j in self._unmatched_gt:
            r = self._gt[j]
            best_i = int(self._sim[:, j].argmax())
            vals = [
                r.get("id", ""),
                r.get("type", ""),
                _shorten(r.get("description", "")),
                round(float(self._sim[:, j].max()), 4),
                self._llm[best_i].get("id", ""),
            ]
            self._fn_tree.insert("", "end", iid=str(j), values=vals)

    # ------------------------------------------------------------------
    # Manual matching
    # ------------------------------------------------------------------

    def _add_match(self):
        """Create a match from one selected unmatched LLM + one unmatched GT."""
        fp_sel = self._fp_tree.selection()
        fn_sel = self._fn_tree.selection()
        if not fp_sel or not fn_sel:
            self._match_status.config(
                text="Select one row from EACH unmatched list, then Add Match.",
                foreground="red")
            return

        llm_idx = int(fp_sel[0])
        gt_idx  = int(fn_sel[0])
        # Similarity for this pair is already in the precomputed matrix.
        score = float(self._sim[llm_idx, gt_idx])

        self._pairs.append((llm_idx, gt_idx, score))
        self._manual_keys.add((llm_idx, gt_idx))
        self._match_status.config(
            text=f"Matched LLM[{self._llm[llm_idx].get('id','')}] ↔ "
                 f"GT[{self._gt[gt_idx].get('id','')}]  (sim={score:.4f})",
            foreground="green")
        self._refresh_report()

    def _unmatch_selected(self):
        """Break the selected matched pair; both items return to the unmatched lists."""
        sel = self._matched_tree.selection()
        if not sel:
            self._unmatch_status.config(
                text="Select a matched pair above to break it.", foreground="red")
            return
        # iid encodes the exact pair as "<llm_idx>_<gt_idx>" (both are integers).
        i_str, j_str = sel[0].split("_")
        i, j = int(i_str), int(j_str)

        self._pairs = [p for p in self._pairs if not (p[0] == i and p[1] == j)]
        self._manual_keys.discard((i, j))
        self._unmatch_status.config(
            text=f"Unmatched LLM[{self._llm[i].get('id','')}] ↔ "
                 f"GT[{self._gt[j].get('id','')}] — both back in the lists below.",
            foreground="orange")
        self._refresh_report()

    # ------------------------------------------------------------------
    # Save
    # ------------------------------------------------------------------

    def _save_report(self):
        if self._report is None:
            messagebox.showinfo("Nothing to save", "Run an evaluation first.")
            return
        out_path = self._out_var.get().strip()
        if not out_path:
            out_path = filedialog.asksaveasfilename(
                defaultextension=".xlsx", filetypes=[("Excel files", "*.xlsx")])
        if not out_path:
            return
        try:
            report = self._report
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with pd.ExcelWriter(out_path, engine="openpyxl") as w:
                desc_df  = report["Field_Metrics_Desc"]
                other_df = report["Field_Metrics_Other"]
                desc_df.to_excel(w, sheet_name="Field_Metrics", index=False, startrow=0)
                other_df.to_excel(w, sheet_name="Field_Metrics", index=False,
                                  startrow=len(desc_df) + 3)
                for sheet in ("Field_Counts", "Requirement_Matching",
                              "Matched_TP", "False_Positives", "False_Negatives"):
                    report[sheet].to_excel(w, sheet_name=sheet, index=False)
            self._status.config(text=f"Saved → {out_path}", foreground="green")
        except Exception as exc:
            messagebox.showerror("Save error", str(exc))


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = EvaluatorGUI()
    app.mainloop()
