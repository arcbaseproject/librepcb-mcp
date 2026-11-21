# LED Example

5 V LED indicator: TP1 (+5V) → R1 330 Ω (0603) → LED1 red 3 mm THT → TP2 (GND).
Board: 30 × 20 mm, two layers, routed on top copper.

Generated with librepcb-mcp tools only. Verified with LibrePCB 2.1.1:
`librepcb-cli open-project --strict --erc --drc "LED Example.lpp"` → 0 ERC / 0 DRC messages.

Library elements in `library/` come from LibrePCB_Base.lplib (CC0-1.0).
Not reviewed for manufacturing.
