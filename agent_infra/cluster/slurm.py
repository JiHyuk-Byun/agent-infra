"""SLURM cluster provider implementation."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from typing import Any, Optional

from agent_infra.cluster.base import ClusterProvider


def run(cmd: str) -> str:
    """Run a shell command and return stdout."""
    return subprocess.check_output(cmd, shell=True, text=True)


def command_available(cmd: str) -> bool:
    """Check if a command is available in PATH."""
    return shutil.which(cmd) is not None


def get_jobs(user: Optional[str] = None) -> list[tuple[str, str]]:
    """Return job info from squeue with jobid + node."""
    if user:
        out = run(f"squeue -u {user} -h -o '%i %N'")
    else:
        out = run("squeue -u $USER -h -o '%i %N'")

    jobs = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        job_id = parts[0]
        node = parts[1] if len(parts) > 1 else "UNKNOWN"
        jobs.append((job_id, node))
    return jobs


def get_job_info(job_id: str) -> str:
    """Return full scontrol dump for a job."""
    return run(f"scontrol show job {job_id}")


def extract_command(scontrol_text: str) -> Optional[str]:
    """Extract Command=... from scontrol output."""
    m = re.search(r"Command=(.*)", scontrol_text)
    if not m:
        return None
    cmd = m.group(1).strip()

    # Strip trailing SLURM metadata fields
    stop_tokens = [
        "RunTime=", "WorkDir=", "BatchFlag=", "UserId=", "JobState=",
        "TRES=", "StdErr=", "StdIn=", "StdOut=", "Container=", "Priority=",
    ]
    for token in stop_tokens:
        if token in cmd:
            cmd = cmd.split(token)[0].strip()

    return cmd


def extract_nodes(scontrol_text: str) -> Optional[str]:
    """Extract NodeList=... field."""
    m = re.search(r"NodeList=([\w\-,]+)", scontrol_text)
    return m.group(1) if m else None


def extract_partition(scontrol_text: str) -> Optional[str]:
    """Extract Partition=... field."""
    m = re.search(r"Partition=([\w\-]+)", scontrol_text)
    return m.group(1) if m else None


def get_slurm_jobs(user: Optional[str] = None) -> Optional[dict[str, dict]]:
    """Get all SLURM jobs with detailed info.

    Returns:
        Dict mapping job_id to info dict, or None if SLURM unavailable
    """
    if not command_available("squeue"):
        print("[INFO] Slurm is not installed.")
        return None

    jobs = get_jobs(user)
    if not jobs:
        print("[INFO] No jobs found.")
        return None

    results = {}
    for job_id, node_from_squeue in jobs:
        scontrol_text = get_job_info(job_id)
        cmd = extract_command(scontrol_text)
        node_from_scontrol = extract_nodes(scontrol_text)
        partition = extract_partition(scontrol_text)

        results[job_id] = {
            "node": node_from_scontrol or node_from_squeue,
            "command": cmd,
            "partition": partition,
        }
    return results


def get_partition_info() -> dict[str, dict[str, Any]]:
    """Get info about all partitions using sinfo."""
    if not command_available("sinfo"):
        return {}

    try:
        out = run("sinfo -h -o '%P %a %D %A'")
    except subprocess.CalledProcessError:
        return {}

    partitions = {}
    for line in out.strip().splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue

        name = parts[0].rstrip("*")
        state = parts[1]
        total_nodes = int(parts[2])

        alloc_idle = parts[3].split("/")
        if len(alloc_idle) == 2:
            allocated = int(alloc_idle[0])
            idle = int(alloc_idle[1])
        else:
            allocated = 0
            idle = total_nodes

        partitions[name] = {
            "total_nodes": total_nodes,
            "idle_nodes": idle,
            "allocated_nodes": allocated,
            "state": state,
        }

    return partitions


def _parse_gres_count(gres_str: str) -> int:
    """Parse GPU count from GRES string."""
    if not gres_str:
        return 0
    match = re.search(r'gpu:[^:]+:(\d+)', gres_str)
    if match:
        return int(match.group(1))
    match = re.search(r'gpu:(\d+)', gres_str)
    if match:
        return int(match.group(1))
    return 0


def get_partition_gpu_availability(partition: str, gpus_needed: int = 4) -> dict[str, Any]:
    """Get detailed GPU availability for a specific partition."""
    if not command_available("scontrol"):
        return {"available": False, "message": "scontrol not available"}

    try:
        out = run("scontrol show nodes --json")
        nodes_data = json.loads(out)
    except subprocess.CalledProcessError as e:
        return {"available": False, "message": f"Failed to query nodes: {e}"}
    except json.JSONDecodeError as e:
        return {"available": False, "message": f"Failed to parse node data: {e}"}

    total_idle_gpus = 0
    idle_nodes = 0
    node_details = []

    for node in nodes_data.get("nodes", []):
        if partition not in node.get("partitions", []):
            continue

        states = node.get("state", [])
        if any(s in ("DOWN", "DRAIN", "DRAINING", "NOT_RESPONDING") for s in states):
            continue

        gres_total = node.get("gres", "")
        gres_used = node.get("gres_used", "")

        total_gpus = _parse_gres_count(gres_total)
        used_gpus = _parse_gres_count(gres_used)

        if total_gpus == 0:
            continue

        available_gpus = max(0, total_gpus - used_gpus)

        if available_gpus > 0:
            total_idle_gpus += available_gpus
            idle_nodes += 1
            node_details.append(f"{node.get('name', '?')}:{available_gpus}")

    available = total_idle_gpus >= gpus_needed
    detail_str = f" ({', '.join(node_details[:5])}{'...' if len(node_details) > 5 else ''})" if node_details else ""

    return {
        "available": available,
        "idle_gpus": total_idle_gpus,
        "idle_nodes": idle_nodes,
        "gpus_needed": gpus_needed,
        "message": f"{total_idle_gpus} GPUs on {idle_nodes} nodes{detail_str}"
        if available
        else f"Only {total_idle_gpus} GPUs available (need {gpus_needed})",
    }


def select_partition(
    preferences: list[dict[str, Any]], verbose: bool = True
) -> Optional[dict[str, Any]]:
    """Select best available partition based on preference order.

    Args:
        preferences: List of partition configs, each with:
            - partition: str - partition name
            - qos: str - QOS to use
            - gpus: int - GPUs needed per job
        verbose: Print selection process

    Returns:
        Selected partition config dict, or None if none available
    """
    if not command_available("scontrol"):
        if verbose:
            print("[WARN] scontrol not available, using first preference")
        return preferences[0] if preferences else None

    if verbose:
        print("Checking partition availability...")

    for pref in preferences:
        partition = pref["partition"]
        gpus_needed = pref.get("gpus", 4)

        availability = get_partition_gpu_availability(partition, gpus_needed)

        if verbose:
            status = "OK" if availability["available"] else "SKIP"
            print(f"  [{status}] {partition}: {availability['message']}")

        if availability["available"]:
            return pref

    if verbose:
        print("  [WARN] No partitions available, using first preference as fallback")

    return preferences[0] if preferences else None


def allocate_partitions(
    preferences: list[dict[str, Any]],
    num_replicas: int,
    verbose: bool = True,
) -> list[dict[str, Any]]:
    """Allocate replicas across partitions based on availability.

    Distributes replicas starting from most preferred partition,
    filling each partition to capacity before moving to the next.

    Args:
        preferences: List of partition configs
        num_replicas: Total number of replicas to allocate
        verbose: Print allocation process

    Returns:
        List of allocation dicts with partition, qos, gpus, count
    """
    if not command_available("scontrol"):
        if verbose:
            print("[WARN] scontrol not available, using first preference for all")
        if preferences:
            return [{**preferences[0], "count": num_replicas}]
        return []

    if verbose:
        print(f"Allocating {num_replicas} replicas across partitions...")

    allocations = []
    remaining = num_replicas

    for pref in preferences:
        if remaining <= 0:
            break

        partition = pref["partition"]
        gpus_per_replica = pref.get("gpus", 4)

        availability = get_partition_gpu_availability(partition, gpus_per_replica)
        idle_gpus = availability.get("idle_gpus", 0)

        can_fit = idle_gpus // gpus_per_replica
        to_allocate = min(can_fit, remaining)

        if verbose:
            status = f"{to_allocate}/{remaining}" if to_allocate > 0 else "SKIP"
            print(f"  [{status}] {partition}: {idle_gpus} GPUs free, can fit {can_fit} replicas")

        if to_allocate > 0:
            allocations.append({
                "partition": partition,
                "qos": pref.get("qos", "default"),
                "gpus": gpus_per_replica,
                "count": to_allocate,
            })
            remaining -= to_allocate

    if remaining > 0:
        if verbose:
            print(f"  [WARN] Could not allocate {remaining} replicas across available partitions")
        if allocations:
            fallback = max(allocations, key=lambda a: a["count"])
            fallback["count"] += remaining
            if verbose:
                print(f"  [FALLBACK] Adding {remaining} to {fallback['partition']} (best available)")
        else:
            if verbose:
                print("  [ERROR] No partitions have available GPUs. Cannot allocate any replicas.")

    if verbose and allocations:
        print("  Allocation plan:")
        for alloc in allocations:
            print(f"    {alloc['partition']}: {alloc['count']} replica(s)")

    return allocations


def build_sbatch_cmd(partition_config: dict[str, Any]) -> list[str]:
    """Build sbatch command from partition config."""
    return [
        "sbatch",
        "-p", partition_config["partition"],
        "-q", partition_config.get("qos", "default"),
        "--gres", f"gpu:{partition_config.get('gpus', 4)}",
    ]


class SlurmProvider(ClusterProvider):
    """SLURM cluster provider implementation."""

    def __init__(self, partition_preferences: list[dict[str, Any]]):
        """Initialize SLURM provider.

        Args:
            partition_preferences: Ordered list of partition configs
        """
        self.partition_preferences = partition_preferences

    def is_available(self) -> bool:
        return command_available("squeue")

    def allocate(
        self,
        model_name: str,
        gpus_per_replica: int,
        num_replicas: int,
    ) -> list[dict[str, Any]]:
        # Update preferences with requested GPU count
        prefs = [
            {**p, "gpus": gpus_per_replica}
            for p in self.partition_preferences
        ]
        return allocate_partitions(prefs, num_replicas)

    def submit_job(
        self,
        script_path: str,
        allocation: dict[str, Any],
    ) -> str:
        cmd = build_sbatch_cmd(allocation)
        cmd.append(script_path)
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        # Parse job ID from "Submitted batch job XXXXX"
        match = re.search(r'Submitted batch job (\d+)', result.stdout)
        return match.group(1) if match else result.stdout.strip()

    def list_jobs(self, user: Optional[str] = None) -> Optional[dict[str, dict]]:
        return get_slurm_jobs(user)

    def cancel_job(self, job_id: str) -> bool:
        try:
            subprocess.run(["scancel", job_id], check=True)
            return True
        except subprocess.CalledProcessError:
            return False
