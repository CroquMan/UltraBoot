"""
pc_monitor.py — Lit les capteurs du PC et pousse un status.json vers GitHub
pour alimenter le dashboard de télémétrie hébergé sur GitHub Pages.

Prérequis :
  - Python 3.9+
  - pip install psutil requests
  - LibreHardwareMonitor lancé en arrière-plan (en administrateur) avec
    l'option "Remote Web Server" activée (port par défaut : 8085)
  - Un dépôt git local déjà cloné et configuré (voir le guide de mise en place)

Ce script tourne en boucle infinie : il lit les capteurs, écrit status.json
dans le dépôt local, puis commit + force-push (amend) pour ne garder qu'un
seul commit dans l'historique et éviter de faire gonfler le dépôt.
"""

import json
import subprocess
import time
import logging
from pathlib import Path

import psutil
import requests

# ============================================================
# CONFIGURATION — à adapter
# ============================================================

REPO_PATH = Path(r"C:\UltraBoot")   # dossier du dépôt git local
LHM_URL = "http://localhost:8085/data.json"      # URL du Remote Web Server de LibreHardwareMonitor
PUSH_INTERVAL_SEC = 20                           # entre 15 et 30s conseillé
LOG_FILE = REPO_PATH / "monitor.log"

# Mots-clés utilisés pour repérer le bon capteur dans l'arbre LibreHardwareMonitor.
# Si l'auto-détection ne trouve pas tes composants, lance le script avec
# DEBUG_PRINT_TREE = True pour voir les noms exacts renvoyés par LHM et
# ajuste ces listes en conséquence.
CPU_HARDWARE_KEYWORDS = ["ryzen", "intel", "core i", "amd cpu"]
GPU_HARDWARE_KEYWORDS = ["nvidia", "geforce", "radeon", "rtx", "gtx"]
CPU_TEMP_PREFERRED = ["package", "tctl", "tdie", "die"]
GPU_TEMP_PREFERRED = ["core", "hot spot", "gpu core"]

DEBUG_PRINT_TREE = False  # passe à True une seule fois pour inspecter l'arbre LHM

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


# ============================================================
# Lecture LibreHardwareMonitor
# ============================================================

def fetch_lhm_tree():
    r = requests.get(LHM_URL, timeout=5)
    r.raise_for_status()
    return r.json()


def flatten_sensors(node, path=None):
    """Retourne une liste de (path_list, value_str) pour chaque capteur feuille."""
    if path is None:
        path = []
    text = node.get("Text", "")
    children = node.get("Children", []) or []
    new_path = path + [text]
    results = []
    if not children and "Value" in node and node["Value"]:
        results.append((new_path, node["Value"]))
    for c in children:
        results.extend(flatten_sensors(c, new_path))
    return results


def parse_numeric(value_str):
    """'61.0 °C' -> 61.0 ; '42.3 %' -> 42.3 ; '3 200 MB' -> 3200.0"""
    if not value_str:
        return None
    cleaned = value_str.replace("\u00a0", " ").replace(",", ".")
    num = ""
    for ch in cleaned:
        if ch.isdigit() or ch == "." or ch == "-":
            num += ch
        elif num:
            break
    try:
        return float(num)
    except ValueError:
        return None


def match_hardware(sensors, keywords):
    """Filtre les capteurs dont le chemin contient un des mots-clés (niveau matériel)."""
    out = []
    for path, value in sensors:
        # le nom du matériel est en général au 3e niveau (Sensor > Hôte > Matériel > ...)
        hw_name = path[2].lower() if len(path) > 2 else ""
        if any(k in hw_name for k in keywords):
            out.append((path, value))
    return out


def best_temp(sensors_subset, preferred_keywords):
    temps = [(p, parse_numeric(v)) for p, v in sensors_subset if "°c" in v.lower()]
    temps = [(p, v) for p, v in temps if v is not None]
    if not temps:
        return None
    for p, v in temps:
        leaf = p[-1].lower()
        if any(k in leaf for k in preferred_keywords):
            return v
    # fallback : la température max relevée (le pire cas, pertinent pour une alerte)
    return max(v for _, v in temps)


def best_load(sensors_subset, leaf_keyword):
    loads = [(p, parse_numeric(v)) for p, v in sensors_subset if "%" in v]
    loads = [(p, v) for p, v in loads if v is not None]
    if not loads:
        return None
    for p, v in loads:
        if leaf_keyword in p[-1].lower():
            return v
    return max(v for _, v in loads) if loads else None


def get_vram(sensors_subset):
    used = total = None
    for path, value in sensors_subset:
        leaf = path[-1].lower()
        if "memory used" in leaf:
            used = parse_numeric(value)
        elif "memory total" in leaf:
            total = parse_numeric(value)
    return used, total


def get_hardware_name(sensors_subset):
    if not sensors_subset:
        return None
    path = sensors_subset[0][0]
    return path[2] if len(path) > 2 else None


def read_gpu_cpu_metrics():
    tree = fetch_lhm_tree()
    sensors = flatten_sensors(tree)

    if DEBUG_PRINT_TREE:
        for path, value in sensors:
            print(" > ".join(path), "=", value)

    cpu_sensors = match_hardware(sensors, CPU_HARDWARE_KEYWORDS)
    gpu_sensors = match_hardware(sensors, GPU_HARDWARE_KEYWORDS)

    cpu = {
        "name": get_hardware_name(cpu_sensors) or "CPU",
        "temp": best_temp(cpu_sensors, CPU_TEMP_PREFERRED),
        "usage": best_load(cpu_sensors, "cpu total"),
    }

    vram_used, vram_total = get_vram(gpu_sensors)
    gpu = {
        "name": get_hardware_name(gpu_sensors) or "GPU",
        "temp": best_temp(gpu_sensors, GPU_TEMP_PREFERRED),
        "usage": best_load(gpu_sensors, "gpu core"),
        "vram_used": vram_used,
        "vram_total": vram_total,
    }
    return cpu, gpu


# ============================================================
# Métriques système (psutil)
# ============================================================

def read_ram():
    vm = psutil.virtual_memory()
    return {
        "used": round(vm.used / (1024 ** 3), 1),
        "total": round(vm.total / (1024 ** 3), 1),
        "usage_pct": vm.percent,
    }


def read_disks():
    disks = []
    for part in psutil.disk_partitions(all=False):
        if "cdrom" in part.opts or not part.fstype:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        disks.append({
            "name": part.device.rstrip("\\"),
            "used_pct": usage.percent,
        })
    return disks


_last_net = {"t": None, "sent": None, "recv": None}


def read_network():
    counters = psutil.net_io_counters()
    now = time.time()
    up_kbps = down_kbps = 0.0
    if _last_net["t"] is not None:
        dt = max(now - _last_net["t"], 1e-6)
        up_kbps = (counters.bytes_sent - _last_net["sent"]) / dt * 8 / 1000
        down_kbps = (counters.bytes_recv - _last_net["recv"]) / dt * 8 / 1000
    _last_net.update(t=now, sent=counters.bytes_sent, recv=counters.bytes_recv)
    return {"up_kbps": round(max(up_kbps, 0), 1), "down_kbps": round(max(down_kbps, 0), 1)}


# ============================================================
# Git push (amend + force, pour ne garder qu'un seul commit)
# ============================================================

def git(*args):
    result = subprocess.run(
        ["git", *args], cwd=REPO_PATH, capture_output=True, text=True
    )
    if result.returncode != 0:
        logging.warning("git %s -> %s", args, result.stderr.strip())
    return result


def push_status(data):
    status_path = REPO_PATH / "status.json"
    status_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    git("add", "status.json")
    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"], cwd=REPO_PATH
    )
    if diff.returncode == 0:
        return  # rien n'a changé (rare, mais évite un commit vide)

    git("commit", "--amend", "--no-edit", "-q")
    git("push", "--force", "-q")


# ============================================================
# Boucle principale
# ============================================================

def main():
    logging.info("Démarrage du monitoring PC")
    psutil.cpu_percent(interval=None)  # premier appel "à vide", requis par psutil

    while True:
        try:
            cpu, gpu = read_gpu_cpu_metrics()
        except Exception as e:
            logging.error("Erreur lecture LibreHardwareMonitor: %s", e)
            cpu, gpu = {"name": "CPU", "temp": None, "usage": psutil.cpu_percent()}, {}

        data = {
            "timestamp": int(time.time()),
            "hostname": psutil.os.environ.get("COMPUTERNAME", "PC"),
            "cpu": cpu,
            "gpu": gpu,
            "ram": read_ram(),
            "disks": read_disks(),
            "network": read_network(),
        }

        try:
            push_status(data)
        except Exception as e:
            logging.error("Erreur push git: %s", e)

        time.sleep(PUSH_INTERVAL_SEC)


if __name__ == "__main__":
    main()
