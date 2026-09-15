#!/usr/bin/env bash
#
# Deployment der WB-Module auf s02.
#
# Die Module liegen auf s02 NICHT als Kopie, sondern als Symlinks in einen
# Git-Klon:
#
#   /opt/odoo19/custom_addons/wb_subscription
#       -> /opt/odoo19/src/wb_License_System/wb_subscription
#
# Deshalb wird ausgerollt, indem der Klon auf den gewuenschten Stand gebracht
# wird. Das fruehere Verfahren (tar entpacken nach rm -rf) haette die Symlinks
# zerstoert und die Quellstruktur zerrissen.
#
# Der Dienst wird IMMER wieder gestartet, auch wenn das Update oder die Tests
# scheitern. Ohne diese Zusicherung bleibt Odoo bei einem Testfehler unten
# (passiert am 15.09.2026).
#
# Aufruf (vom Repo-Wurzelverzeichnis, lokal):
#   scripts/deploy-s02.sh                          # aktueller Branch, ohne Tests
#   scripts/deploy-s02.sh --branch main
#   scripts/deploy-s02.sh --tests wb_hmac
#   scripts/deploy-s02.sh --modules wb_subscription --tests wb_hmac --db Main
#   scripts/deploy-s02.sh --dry-run
#
set -euo pipefail

HOST="${WB_DEPLOY_HOST:-s02}"
SRC_DIR="/opt/odoo19/src/wb_License_System"
ADDONS_DIR="/opt/odoo19/custom_addons"
ODOO_LOG="/var/log/odoo/odoo19.log"
SERVICE="odoo19"

BRANCH=""
DB="Main"
MODULES="wb_subscription"
TEST_TAGS=""
DRY_RUN=0

usage() {
    sed -n '3,25p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        --branch)  BRANCH="$2"; shift 2 ;;
        --db)      DB="$2"; shift 2 ;;
        --modules) MODULES="$2"; shift 2 ;;
        --tests)   TEST_TAGS="$2"; shift 2 ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage 0 ;;
        *) echo "Unbekannte Option: $1" >&2; usage 1 ;;
    esac
done

if [ -z "$BRANCH" ]; then
    BRANCH="$(git rev-parse --abbrev-ref HEAD)"
fi

echo "Ziel:     $HOST"
echo "Branch:   $BRANCH"
echo "DB:       $DB"
echo "Module:   $MODULES"
echo "Tests:    ${TEST_TAGS:-keine}"
echo

# Ungepushte Aenderungen wuerden auf dem Server fehlen: der Klon zieht aus
# GitHub, nicht von diesem Rechner.
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "WARNUNG: lokale Aenderungen sind nicht committet und landen nicht auf $HOST." >&2
fi
if [ -n "$(git log "origin/$BRANCH..$BRANCH" --oneline 2>/dev/null || true)" ]; then
    echo "WARNUNG: lokale Commits sind nicht gepusht und landen nicht auf $HOST." >&2
fi

if [ "$DRY_RUN" = "1" ]; then
    echo "Trockenlauf, es wird nichts ausgefuehrt."
    exit 0
fi

# Das eigentliche Ausrollen laeuft in einer Sitzung auf dem Server, damit der
# trap auch dann greift, wenn die Verbindung mittendrin abbricht.
ssh "$HOST" \
    SRC_DIR="$SRC_DIR" ADDONS_DIR="$ADDONS_DIR" ODOO_LOG="$ODOO_LOG" \
    SERVICE="$SERVICE" BRANCH="$BRANCH" DB="$DB" MODULES="$MODULES" \
    TEST_TAGS="$TEST_TAGS" 'bash -s' <<'REMOTE'
set -uo pipefail

dienst_starten() {
    systemctl start "$SERVICE" || true
    sleep 6
    local zustand
    zustand="$(systemctl is-active "$SERVICE" || true)"
    local code
    code="$(curl -sS -o /dev/null -w '%{http_code}' -m 20 http://127.0.0.1:8069/web/login || echo 000)"
    echo "Dienst: $zustand, HTTP $code"
    [ "$zustand" = "active" ] && [ "$code" = "200" ]
}

# Egal wie dieses Skript endet: Odoo laeuft danach wieder.
trap 'dienst_starten || echo "ACHTUNG: Dienst kam nicht sauber hoch." >&2' EXIT

cd "$SRC_DIR"

if [ -n "$(git status --porcelain)" ]; then
    echo "Abbruch: Arbeitsverzeichnis auf dem Server ist nicht sauber." >&2
    git status --short >&2
    exit 1
fi

git fetch -q origin
git checkout -q "$BRANCH"
git pull -q --ff-only
echo "Stand: $(git log --oneline -1)"

# Nicht-Odoo-Verzeichnisse (etwa wb_license_client_php mit den Interop-Vektoren)
# als Symlink bereitstellen. Ohne Manifest laedt Odoo sie nicht, die Tests
# finden ueber diesen Pfad aber ihre Datendateien.
for extra in wb_license_client_php; do
    if [ -d "$SRC_DIR/$extra" ]; then
        ln -sfn "$SRC_DIR/$extra" "$ADDONS_DIR/$extra"
        chown -h odoo19:odoo19 "$ADDONS_DIR/$extra"
    fi
done

find "$SRC_DIR" -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true

# Nur installierte Module aktualisieren. Ein nicht installiertes Modul in -u
# wird stillschweigend uebergangen, und man haelt dessen Tests faelschlich
# fuer gelaufen.
zu_aktualisieren=""
for modul in ${MODULES//,/ }; do
    zustand="$(sudo -u postgres psql -d "$DB" -tAc \
        "select state from ir_module_module where name='$modul';" 2>/dev/null | tr -d ' ')"
    case "$zustand" in
        installed) zu_aktualisieren="${zu_aktualisieren:+$zu_aktualisieren,}$modul" ;;
        "")        echo "Hinweis: $modul ist in $DB unbekannt, wird uebersprungen." ;;
        *)         echo "Hinweis: $modul ist '$zustand', nicht installiert, wird uebersprungen." ;;
    esac
done

if [ -z "$zu_aktualisieren" ]; then
    echo "Kein installiertes Modul zu aktualisieren. Code ist ausgerollt, Dienst bleibt unberuehrt."
    exit 0
fi

echo "Aktualisiere: $zu_aktualisieren"
marke="$(date '+%Y-%m-%d %H:%M:%S')"

systemctl stop "$SERVICE"

test_argumente=()
if [ -n "$TEST_TAGS" ]; then
    test_argumente=(--test-enable --test-tags "$TEST_TAGS")
fi

sudo -u odoo19 /opt/odoo19/venv/bin/python3.12 /opt/odoo19/odoo-bin \
    -c /etc/odoo19.conf -d "$DB" -u "$zu_aktualisieren" \
    "${test_argumente[@]}" --stop-after-init --no-http
rc=$?

echo "odoo-bin Exit: $rc"

# Odoo schreibt nach logfile aus der Config, nicht nach stdout. Wer nur stdout
# auswertet, sieht bei einem Fehlschlag nichts.
echo "--- Meldungen aus $ODOO_LOG seit $marke:"
awk -v ab="$marke" '$0 >= ab' "$ODOO_LOG" 2>/dev/null \
    | grep -E "ERROR|CRITICAL|failures|odoo.tests.stats" \
    | grep -v "Mute this logger" \
    | tail -20

exit $rc
REMOTE

rc=$?
echo
if [ $rc -eq 0 ]; then
    echo "Ausgerollt. Modul-Update und Tests sauber."
else
    echo "Fehlgeschlagen (Exit $rc). Dienst wurde trotzdem gestartet, Meldungen oben." >&2
fi
exit $rc
