"""Zeitraster der Radarbilder (5-Minuten-Schritte), wie im Plasmoid."""
import time

STEP_SECONDS = 300
# Der DWD braucht einige Minuten, bis ein Bild veröffentlicht ist.
SAFETY_SECONDS = 600
FUTURE_FRAMES = 24  # 2 Stunden Vorhersage

WEEKDAYS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]


def base_time(now=None):
    """Jüngster Zeitpunkt, für den der DWD sicher schon Daten hat."""
    if now is None:
        now = time.time()
    t = int(now) - SAFETY_SECONDS
    return t - t % STEP_SECONDS


def frame_times(base, past_frames, with_forecast=True):
    """Liste der Zeitpunkte (Epoch, UTC).

    Index past_frames ist das "Jetzt"-Bild (= base). Davor liegt der Verlauf,
    danach die Vorhersage.
    """
    times = [base - (past_frames - i) * STEP_SECONDS for i in range(past_frames)]
    times.append(base)
    if with_forecast:
        times += [base + i * STEP_SECONDS for i in range(1, FUTURE_FRAMES + 1)]
    return times


def iso_utc(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def local_label(ts):
    t = time.localtime(ts)
    return "%s, %02d.%02d. %02d:%02d" % (
        WEEKDAYS[t.tm_wday], t.tm_mday, t.tm_mon, t.tm_hour, t.tm_min)


def relative_label(ts, base):
    """Kurzer Hinweis wie "Jetzt", "-45 min" oder "+30 min"."""
    minutes = (ts - base) // 60
    if minutes == 0:
        return "Jetzt"
    return "%+d min" % minutes
