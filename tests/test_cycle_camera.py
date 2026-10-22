import queue
import time

import numpy as np

from marker_engine.camera import CameraWorker, FrameStore, ImageSource
from marker_engine.cycle import (ACQUIRING, CAMERA_DOWN, IDLE, WAIT_CABLE, WAIT_REMOVAL, FrameResult,
                                    InspectionCycle)
from marker_engine.detection import ABSENT, PRESENT, Measurement
from marker_engine.evaluation import CAMERA_ERROR, FAIL, INDETERMINATE, NO_CABLE, PASS, Verdict

CFG = {"frames_required": 3, "presence_frames": 2, "removal_frames": 3, "cycle_timeout_s": 2.0}


def fr(seq, status=PASS, present=True, top=10.0):
    m = Measurement(presence=PRESENT if present else ABSENT, edge_top=top, edge_bottom=top + 40)
    return FrameResult(seq, seq * 0.1, m, Verdict(status=status if present else NO_CABLE))


def feed(cycle, frames, t0=0.0):
    results = []
    for i, f in enumerate(frames):
        r = cycle.on_frame(f, t0 + i * 0.1)
        if r:
            results.append(r)
    return results


def test_manual_cycle_uses_only_fresh_frames_and_latches():
    c = InspectionCycle(CFG)
    c.trigger(0.0, last_seq=10)
    # frames 9 and 10 are stale (captured before the trigger) and must be ignored
    res = feed(c, [fr(9, FAIL), fr(10, FAIL), fr(11), fr(12), fr(13)])
    assert len(res) == 1 and res[0].status == PASS and res[0].frames_used == 3
    assert c.phase == IDLE and c.latched is res[0]
    assert feed(c, [fr(14, FAIL), fr(15, FAIL), fr(16, FAIL)]) == []   # no new trigger -> no new result


def test_new_trigger_clears_previous_result():
    c = InspectionCycle(CFG)
    c.trigger(0.0, 0)
    feed(c, [fr(1), fr(2), fr(3)])
    c.trigger(1.0, 3)
    assert c.latched is None and c.phase == ACQUIRING


def test_inconsistent_frames_give_indeterminate():
    c = InspectionCycle(CFG)
    c.trigger(0.0, 0)
    res = feed(c, [fr(1), fr(2, FAIL), fr(3)])
    assert res[0].status == INDETERMINATE
    assert "inconsistent" in res[0].verdict.reasons[0]


def test_moving_cable_restarts_stable_set_and_times_out():
    c = InspectionCycle(CFG)
    c.trigger(0.0, 0)
    frames = [fr(i, top=10.0 + 8 * i) for i in range(1, 15)]  # moves 8 px per frame
    assert feed(c, frames) == []
    res = c.tick(5.0)
    assert res.status == INDETERMINATE and "timeout" in res.verdict.reasons[0]


def test_auto_mode_needs_removal_before_next_inspection():
    c = InspectionCycle(CFG)
    c.set_auto(True)
    assert c.phase == WAIT_CABLE
    res = feed(c, [fr(1), fr(2), fr(3), fr(4), fr(5)])
    assert [r.status for r in res] == [PASS]
    assert c.phase == WAIT_REMOVAL
    # cable stays: no new inspection however long it stays
    assert feed(c, [fr(i) for i in range(6, 30)]) == []
    # removed for 3 frames, then a new cable appears -> new cycle
    feed(c, [fr(30, present=False), fr(31, present=False), fr(32, present=False)])
    assert c.phase == WAIT_CABLE
    res = feed(c, [fr(33, FAIL), fr(34, FAIL), fr(35, FAIL), fr(36, FAIL)])
    assert [r.status for r in res] == [FAIL]


def test_camera_error_replaces_latched_pass_and_forces_fresh_inspection():
    c = InspectionCycle(CFG)
    c.set_auto(True)
    feed(c, [fr(1), fr(2), fr(3), fr(4), fr(5)])
    assert c.latched.status == PASS
    err = c.on_camera_error("camera lost", 2.0)
    assert err.status == CAMERA_ERROR and c.latched.status == CAMERA_ERROR
    assert c.phase == CAMERA_DOWN
    # camera back with the same cable still present: it is inspected again, PASS is not reused
    res = feed(c, [fr(10), fr(11), fr(12), fr(13), fr(14)], t0=3.0)
    assert len(res) == 1 and res[0].trigger == "auto"


def test_camera_error_during_acquisition():
    c = InspectionCycle(CFG)
    c.trigger(0.0, 0)
    feed(c, [fr(1)])
    res = c.on_camera_error("read error", 0.5)
    assert res.status == CAMERA_ERROR and c.phase == CAMERA_DOWN


class FlakySource:
    name = "flaky"
    is_live = True
    frame_interval = 0.01

    def __init__(self, good_frames=10):
        self.n = 0
        self.good = good_frames

    def open(self):
        pass

    def read(self):
        self.n += 1
        return np.zeros((8, 8, 3), np.uint8) if self.n <= self.good else None

    def release(self):
        pass


def test_camera_disconnect_is_reported():
    store, events = FrameStore(), queue.Queue()
    worker = CameraWorker(FlakySource(), store, events, reconnect_s=5.0, max_failures=3)
    worker.start()
    first = store.wait_newer(0, 2.0)
    assert first is not None
    deadline = time.time() + 3
    while store.camera_ok and time.time() < deadline:
        time.sleep(0.02)
    assert not store.camera_ok and "lost" in store.error
    assert store.wait_newer(first.seq, 0.2) is None      # no stale frame served as fresh
    worker.stop()
    worker.join(2)
    kinds = [events.get()[0] for _ in range(events.qsize())]
    assert "camera_error" in kinds


def test_image_source_produces_fresh_sequence():
    store = FrameStore()
    worker = CameraWorker(ImageSource(np.ones((4, 4, 3), np.uint8), fps=50), store)
    worker.start()
    a = store.wait_newer(0, 1.0)
    b = store.wait_newer(a.seq, 1.0)
    worker.stop()
    worker.join(2)
    assert b.seq > a.seq
