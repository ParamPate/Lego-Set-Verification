"""
Save and resume in-progress verification jobs.
"""

import json
import os
import time
from pathlib import Path
import tempfile

import pandas as pd


def _job_path(jobs_dir, job_id):
    return Path(jobs_dir) / f"{job_id}.json"


def make_job_id(set_num):
    set_num = str(set_num).strip().replace("/", "-")
    return f"{set_num}_{int(time.time())}"


def save_job(
        jobs_dir,
        job_id,
        set_num,
        user_input_df,
        status="in_progress",
        substitutions=None,
        declined_substitutions=None,
        variation_proposals_df=None,
        import_have_df=None,
        extra_part_metadata_df=None,
        wanted_list_remarks_df=None,
        removal_states_df=None,
        workflow_stage=None,
        report_snapshot_df=None,
        wanted_list_snapshot_df=None,
        import_history=None):
    Path(jobs_dir).mkdir(parents=True, exist_ok=True)

    # Declined offers are stored alongside accepted ones so the parts table can
    # still explain its highlighting after a job is resumed.
    job_data = {
        "job_id": job_id,
        "set_num": set_num,
        "status": status,
        "workflow_stage": workflow_stage or "parts_entry",
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "user_input": user_input_df.to_dict(orient="records"),
        "substitutions": substitutions or [],
        "declined_substitutions": declined_substitutions or [],
        # These two frames make variation decisions reversible after resume.
        # Older jobs omit them and are reconstructed by the UI from the saved
        # choice records.
        "variation_proposals": _dataframe_records(variation_proposals_df),
        "import_have": _dataframe_records(import_have_df),
        # Names and exact-color image URLs only; image bytes are never stored.
        "extra_part_metadata": _dataframe_records(extra_part_metadata_df),
        # Operator-entered bag numbers or notes keyed by part and color.
        "wanted_list_remarks": _dataframe_records(wanted_list_remarks_df),
        # Auditable extra-part removal decisions keyed by part/color/quantity.
        "removal_states": _dataframe_records(removal_states_df),
        # Each accepted CSV records its content hash, merge mode, and exact
        # pre-allocation contribution so duplicate scans can be detected and a
        # future replace operation can be provenance-aware.
        "import_history": import_history or [],
        # Exact derived views let report-review and completed jobs resume
        # without repeating comparison or API enrichment.
        "report_snapshot": _dataframe_records(report_snapshot_df),
        "wanted_list_snapshot": _dataframe_records(wanted_list_snapshot_df),
    }

    job_path = _job_path(jobs_dir, job_id)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            suffix=".tmp",
            dir=job_path.parent,
            delete=False,
        ) as temporary_file:
            temporary_path = temporary_file.name
            json.dump(job_data, temporary_file, indent=2)

        os.replace(temporary_path, job_path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass

    return job_id


def _dataframe_records(dataframe):
    """Return JSON-safe records for an optional DataFrame."""
    if dataframe is None or dataframe.empty:
        return []

    clean_df = dataframe.astype(object).where(dataframe.notna(), None)
    return clean_df.to_dict(orient="records")


def load_job(jobs_dir, job_id):
    with open(_job_path(jobs_dir, job_id), encoding="utf-8") as f:
        job_data = json.load(f)

    job_data["user_input_df"] = pd.DataFrame(job_data.pop("user_input"))
    job_data["substitutions"] = job_data.get("substitutions") or []
    job_data["declined_substitutions"] = (
        job_data.get("declined_substitutions") or []
    )
    job_data["variation_proposals_df"] = pd.DataFrame(
        job_data.pop("variation_proposals", []) or []
    )
    job_data["import_have_df"] = pd.DataFrame(
        job_data.pop("import_have", []) or []
    )
    job_data["extra_part_metadata_df"] = pd.DataFrame(
        job_data.pop("extra_part_metadata", []) or []
    )
    job_data["wanted_list_remarks_df"] = pd.DataFrame(
        job_data.pop("wanted_list_remarks", []) or []
    )
    job_data["removal_states_df"] = pd.DataFrame(
        job_data.pop("removal_states", []) or []
    )
    job_data["import_history"] = job_data.get("import_history") or []
    report_snapshot = job_data.pop("report_snapshot", None)
    wanted_list_snapshot = job_data.pop("wanted_list_snapshot", None)
    job_data["has_report_snapshot"] = report_snapshot is not None
    job_data["has_wanted_list_snapshot"] = wanted_list_snapshot is not None
    job_data["report_snapshot_df"] = pd.DataFrame(report_snapshot or [])
    job_data["wanted_list_snapshot_df"] = pd.DataFrame(
        wanted_list_snapshot or []
    )

    # Jobs saved before confidence scoring was removed carry an extra key.
    job_data.pop("low_confidence_input", None)

    return job_data


def list_jobs(jobs_dir):
    jobs_dir = Path(jobs_dir)
    if not jobs_dir.exists():
        return []

    jobs = []
    for file in sorted(jobs_dir.glob("*.json"), reverse=True):
        try:
            with open(file, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError, UnicodeDecodeError):
            # One unreadable file should not hide every other saved job.
            continue

        jobs.append({
            "job_id": data.get("job_id"),
            "set_num": data.get("set_num"),
            "status": data.get("status"),
            "updated_at": data.get("updated_at"),
        })
    return jobs


def delete_job(jobs_dir, job_id):
    job_id = str(job_id)
    if not job_id or Path(job_id).name != job_id or job_id in {".", ".."}:
        raise ValueError("Invalid saved job ID.")

    path = _job_path(jobs_dir, job_id)
    if path.exists():
        path.unlink()
