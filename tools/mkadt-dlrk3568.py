#!/usr/bin/env python3
"""Build the Apple Device Tree for ATK-DLRK3568 (ios-sweet port).

MMIO from Alientek Linux 6.1 DTS / U-Boot. Confirm DRAM / FB / CNTFRQ on UART
before --highmem or enabling a real framebuffer present path.

Usage:  mkadt-dlrk3568.py OUT.adt [--dump] [--qemu] [--highmem]
"""
import hashlib
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import adt  # noqa: E402

# Measured on ATK-DLRK3568 4G board (/proc/device-tree/memory):
#   bank0: 0x00200000 + 0x08200000
#   bank1: 0x09400000 + 0xe6c00000  (~3.6 GiB contiguous usable)
PHYS_BASE = 0x09400000
MEM_SIZE = 0xE6C00000
PANIC_LOG_SIZE = 0x100000
# No true >4G bank on this SKU; --highmem reuses the large bank1 window.
HIGH_PHYS_BASE = 0x09400000
HIGH_MEM_SIZE = 0xE6C00000

# Rockchip MMIO base for /arm-io ranges (children regs are relative)
SOC_BASE = 0xF0000000

GICD, GICD_SIZE = 0xFD400000, 0x10000
GICR, GICR_SIZE = 0xFD460000, 0xC0000
UART2, UART2_SIZE = 0xFE660000, 0x100
EMMC, EMMC_SIZE = 0xFE310000, 0x10000

# U-Boot DRM logo: reserved-memory drm-logo@0 addr=0xede00000 size=0x13a000
# 5.5" MIPI 1080x1920 confirmed on device; CNTFRQ=24 MHz (serial 0x16E3600)
FB, FB_SIZE = 0xEDE00000, 0x13A000
FB_W, FB_H, FB_STRIDE = 1080, 1920, 1080 * 4
CANVAS_W, CANVAS_H = 1125, 2436  # present as iPhone X until panel path is ready

TIMEBASE = 24000000
VTIMER_INTID = 27

BOARD_ECID = 0x444C524B00000001
BOOT_MANIFEST_HASH = hashlib.sha384(b"dlrk3568 boot manifest").digest()
D22AP_ADT = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "../research/ipsw-x/d22ap.adt"
)

QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE = 0x08000000, 0x080A0000, 62500000
QEMU_VTIMER_INTID = 27
QEMU_SOC_BASE = 0  # QEMU uses absolute regs in virt; keep relative 0


def d22ap_product(path=D22AP_ADT):
    if not os.path.exists(path):
        print(f"warning: {path} missing, no /product node", file=sys.stderr)
        return None
    root, _ = adt.parse(open(path, "rb").read())
    for c in root.children:
        if dict((k, v) for k, f, v in c.props).get("name") == b"product\0":
            return [(k, v) for k, f, v in c.props if not f and k not in ("name", "AAPL,phandle")]
    raise SystemExit(f"{path}: no /product node")


u32 = lambda *v: struct.pack("<%dI" % len(v), *v)
u64 = lambda *v: struct.pack("<%dQ" % len(v), *v)
cstr = lambda s: s.encode() + b"\0"


def node(name, props=(), children=()):
    n = adt.Node()
    n.props = [("name", 0, cstr(name))] + [(k, 0, v) for k, v in props]
    n.children = list(children)
    return n


def build(seed=None, qemu=False, highmem=False):
    phys_base, mem_size = (HIGH_PHYS_BASE, HIGH_MEM_SIZE) if highmem else (PHYS_BASE, MEM_SIZE)
    panic_base = phys_base + mem_size - PANIC_LOG_SIZE
    mem_size -= PANIC_LOG_SIZE
    soc = QEMU_SOC_BASE if qemu else SOC_BASE
    gicd, gicr, timebase = (QEMU_GICD, QEMU_GICR, QEMU_TIMEBASE) if qemu else (GICD, GICR, TIMEBASE)
    vtimer = QEMU_VTIMER_INTID if qemu else VTIMER_INTID
    seed = seed if seed is not None else os.urandom(64)
    phandle = iter(range(1, 1000))
    ph = lambda: ("AAPL,phandle", u32(next(phandle)))

    chosen = node(
        "chosen",
        [
            ph(),
            ("dram-base", u64(phys_base)),
            ("dram-size", u64(mem_size)),
            ("random-seed", seed),
            ("debug-enabled", u32(1)),
            ("embedded-panic-log-size", u32(PANIC_LOG_SIZE)),
            ("firmware-version", cstr("dlrk3568-shim")),
            ("system-firmware-version", cstr("dlrk3568-shim")),
            ("boot-manifest-hash", BOOT_MANIFEST_HASH),
            ("crypto-hash-method", cstr("sha2-384")),
            ("display-scale", u32(3)),
            ("unique-chip-id", struct.pack("<Q", BOARD_ECID)),
            ("chip-id", u32(0x8015)),
            ("certificate-production-status", u32(0)),
            ("effective-production-status-ap", u32(0)),
            ("certificate-security-mode", u32(1)),
            ("effective-security-mode-sep", u32(1)),
        ],
        [
            node("memory-map", [ph()]),
            node("asmb", [ph(), ("lp-sip0", u64(0x6F)), ("lp-sip1", u32(1))]),
        ],
    )

    ncpu = 1 if qemu else 4
    cpu_nodes = []
    for core in range(ncpu):
        cpu_nodes.append(
            node(
                f"cpu{core}",
                [
                    ph(),
                    ("device_type", cstr("cpu")),
                    ("compatible", cstr("arm,cortex-a55") + cstr("ARM,v8")),
                    ("reg", u32(core << 8)),
                    ("cpu-id", u32(core)),
                    ("cluster-id", u32(0)),
                    ("cluster-type", u32(0)),
                    ("die-cluster-id", u32(0)),
                    ("cluster-core-id", u32(core)),
                    ("l2-cache-id", u32(core)),
                    ("l3-cache-id", u32(0)),
                    ("state", cstr("running" if core == 0 else "stopped")),
                    ("timebase-frequency", u32(timebase)),
                    ("clock-frequency", u32(2000000000)),
                    ("bus-frequency", u32(100000000)),
                    ("peripheral-frequency", u32(timebase)),
                    ("fixed-frequency", u32(timebase)),
                ],
            )
        )
    cpus = node("cpus", [ph(), ("#address-cells", u32(1)), ("#size-cells", u32(0))], cpu_nodes)

    gic = node(
        "gic",
        [
            ph(),
            ("compatible", cstr("arm,gic-v3")),
            ("interrupt-controller", b""),
            ("virtual-timer-intid", u32(vtimer)),
            ("reg", u64(gicd - soc, GICD_SIZE, gicr - soc, GICR_SIZE)),
        ],
    )

    uart = node(
        "uart2",
        [
            ph(),
            ("compatible", cstr("snps,dw-apb-uart")),
            ("reg", u64(UART2 - soc if not qemu else UART2, UART2_SIZE)),
            ("reg-shift", u32(2)),
            ("reg-io-width", u32(4)),
        ],
    )

    arm_io = node(
        "arm-io",
        [
            ph(),
            ("device_type", cstr("dlrk3568-io")),
            ("compatible", cstr("dlrk3568-io") + cstr("arm-io")),
            ("#address-cells", u32(2)),
            ("#size-cells", u32(2)),
            ("ranges", u64(0, soc, 0x100000000 - soc) if not qemu else u64(0, 0, 0x100000000)),
        ],
        [
            gic,
            node("wdt", [ph(), ("no-pmu", u32(1))]),
            node(
                "disp0",
                [
                    ph(),
                    ("device_type", cstr("display-subsystem")),
                    ("compatible", cstr("dlrk3568,display")),
                    ("canvas-width", u32(CANVAS_W)),
                    ("canvas-height", u32(CANVAS_H)),
                ],
            ),
        ]
        + ([] if qemu else [uart]),
    )

    fbcon_props = [ph()]
    if FB and FB_SIZE:
        fbcon_props += [
            ("reg", u64(FB, FB_SIZE)),
            ("width", u32(FB_W)),
            ("height", u32(FB_H)),
            ("stride", u32(FB_STRIDE)),
        ]
    # Must be named "sweet-fbcon": xnu patch 0003 probes SecureDTFindEntry("name", "sweet-fbcon")
    fbcon = node("sweet-fbcon", fbcon_props)

    emmc = node(
        "dlrk-emmc",
        [
            ph(),
            ("compatible", cstr("rockchip,rk3568-dwcmshc")),
            ("reg", u64(EMMC, EMMC_SIZE)),
        ],
    )

    defaults = node("defaults", [ph(), ("no-effaceable-storage", u32(1))])
    buttons = node(
        "buttons",
        [
            ph(),
            ("button-names", cstr("volup") + cstr("voldown") + cstr("hold")),
            ("home-button-type", u32(2)),
            ("opposed-power-vol-buttons", u32(1)),
        ],
    )
    pram = node("pram", [ph(), ("reg", u64(panic_base) + u64(PANIC_LOG_SIZE))])

    product_props = d22ap_product()
    product = [node("product", [ph()] + product_props)] if product_props else []

    return node(
        "device-tree",
        [
            ph(),
            (
                "compatible",
                cstr("D22AP")
                + cstr("iPhone10,3")
                + cstr("alientek,dlrk3568")
                + cstr("rockchip,rk3568")
                + cstr("AppleARM"),
            ),
            ("model", cstr("iPhone10,3")),
            ("target-type", cstr("D22")),
            ("target-sub-type", cstr("D22AP")),
            ("manufacturer", cstr("Apple Inc.")),
            ("#address-cells", u32(2)),
            ("#size-cells", u32(2)),
            ("board-model", cstr("ATK-DLRK3568")),
        ],
        [chosen, defaults, cpus, arm_io, fbcon, buttons, pram]
        + product
        + ([] if qemu else [emmc]),
    )


if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1].startswith("-"):
        raise SystemExit("usage: mkadt-dlrk3568.py OUT.adt [--dump] [--qemu] [--highmem]")
    root = build(qemu="--qemu" in sys.argv, highmem="--highmem" in sys.argv)
    blob = adt.serialize(root)
    open(sys.argv[1], "wb").write(blob)
    if "--dump" in sys.argv:
        adt.dump(adt.parse(blob)[0])
    print(
        f"{sys.argv[1]}: {len(blob)} bytes  FB={'set' if FB else 'UNSET'}",
        file=sys.stderr,
    )
