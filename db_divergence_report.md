# BrensanDB divergence report

- **A (app / source):** `C:\Personal\Prog Codes\kicad_library\_Source Code\BrensanDB.sqlite`
- **B (KiCad library root):** `C:\Personal\Prog Codes\kicad_library\BrensanDB.sqlite`

Rows are matched on manufacturer + part number + description, not on `id`,
because the two copies assign ids independently.

## Summary

| Table | A rows | B rows | Only in A | Only in B |
|---|---:|---:|---:|---:|
| Button_Switch | 55 | 55 | 1 | 1 |
| Capacitor | 523 | 513 | 10 | 0 |
| Connector | 357 | 342 | 18 | 3 |
| Crystal | 28 | 26 | 3 | 1 |
| Diode | 274 | 266 | 8 | 2 |
| Fuse | 58 | 58 | 0 | 0 |
| Header | 10 | 8 | 2 | 0 |
| IC | 252 | 248 | 5 | 1 |
| Inductor | 128 | 124 | 4 | 0 |
| MCU | 4 | 3 | 1 | 0 |
| Mechanical | 2 | 2 | 0 | 0 |
| Miscellaneous | 463 | 464 | 0 | 1 |
| Mosfet | 142 | 137 | 6 | 1 |
| Opto | 4 | 4 | 0 | 0 |
| PAD | 2 | 2 | 0 | 0 |
| PTC_NTC | 12 | 12 | 0 | 0 |
| Relay | 5 | 5 | 0 | 0 |
| Resistor | 907 | 895 | 14 | 2 |
| Transistor | 45 | 43 | 2 | 0 |

**Total only in A (source): 74**
**Total only in B (library root): 12**

## Rows only in B (library root) — these are the ones you would lose
## by treating the source copy as authoritative

### Button_Switch (1)
- Brensan_Part_Number=BR-16-104-731; Manufacturer=C&K; Manufacturer_Part_Number=KSC621J LFS; Value=0.05A; Description=SWITCH TACTILE SPST-NO 0.05A 32V 170GF

### Connector (3)
- Brensan_Part_Number=BR-16-105-500; Manufacturer=Amphenol ICC (Commercial Products); Manufacturer_Part_Number=MUSBRM1C130; Description=CONN RCPT TYPEC 24POS PCB R/A
- Brensan_Part_Number=BR-16-105-951; Manufacturer=Samtec; Manufacturer_Part_Number=HLE-108-02-L-DV-LC; Description=CONN RCPT 16POS 0.1 GOLD SMD
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=5024430270; Value=5024430270; Description=CONN RCPT 2POS 0.079 TIN SMD

### Crystal (1)
- Brensan_Part_Number=BR-16-105-446; Manufacturer=Abracon LLC; Manufacturer_Part_Number=ABS06-32.768KHZ-1-T; Value=32.7680KHZ; Description=CRYSTAL 32.7680KHZ 12.5PF SMD

### Diode (2)
- Brensan_Part_Number=BR-16-105-002; Manufacturer=Littelfuse Inc.; Manufacturer_Part_Number=30KPA102CA; Value=102V; Description=TVS DIODE 102VWM 165.6VC P600
- Brensan_Part_Number=BR-16-105-822; Manufacturer=Bourns Inc.; Manufacturer_Part_Number=SMBJ40A; Value=40V; Description=TVS DIODE 40VWM 64.5VC DO214AA

### IC (1)
- Brensan_Part_Number=BR-16-105-737; Manufacturer=Diodes Incorporated; Manufacturer_Part_Number=AP7375Q-33SP-13; Value=3.3V; Description=IC REG LINEAR 3.3V 350MA 8-SO-EP

### Miscellaneous (1)
- Brensan_Part_Number=BR-16-105-828; Manufacturer=KEMET; Manufacturer_Part_Number=SCR39XV-310-1R9B007JH; Value=31A; Description=CMC 640UH 31A 2LN TH AEC-Q200

### Mosfet (1)
- Brensan_Part_Number=BR-16-105-835; Manufacturer=Nexperia USA Inc.; Manufacturer_Part_Number=PSMN1R7-60BS,118; Value=60V; Description=MOSFET N-CH 60V 120A D2PAK

### Resistor (2)
- Brensan_Part_Number=BR-16-105-019; Manufacturer=YAGEO; Manufacturer_Part_Number=RC0603JR-07150KL; Description=RES 150K OHM 5% 1/10W 0603
- Brensan_Part_Number=BR-16-105-578; Manufacturer=YAGEO; Manufacturer_Part_Number=RC0603FR-07133KL; Description=RES 133K OHM 1% 1/10W 0603

## Rows only in A (source) — these are missing from what KiCad reads

### Button_Switch (1)
- Brensan_Part_Number=BR-16-104-731; Manufacturer=C&K; Manufacturer_Part_Number=KSC621J LFS; Value=KSC621J LFS; Description=SWITCH TACTILE SPST-NO 0.05A 32V

### Capacitor (10)
- Brensan_Part_Number=-; Manufacturer=Samsung Electro-Mechanics; Manufacturer_Part_Number=CL10C181JB81PND; Value=180pF; Description=CAP CER 180PF 50V C0G/NP0 0603
- Brensan_Part_Number=-; Manufacturer=TDK; Manufacturer_Part_Number=C2012X7S2A684K125AB; Value=0.68µF; Description=CAP CER 0.68UF 100V X7S 0805
- Brensan_Part_Number=BR-16-101-508; Manufacturer=KEMET; Manufacturer_Part_Number=C0805X103K1GECAUTO; Value=10000pF; Description=CAP CER 0805 10NF 100V C0G 10%
- Brensan_Part_Number=-; Manufacturer=KEMET; Manufacturer_Part_Number=C0805C100J1GACTU; Value=10pF; Description=CAP CER 10PF 100V C0G/NP0 0805
- Brensan_Part_Number=-; Manufacturer=KEMET; Manufacturer_Part_Number=C0805C100J1GACTU; Value=10pF; Description=CAP CER 10PF 100V C0G/NP0 0805
- Brensan_Part_Number=-; Manufacturer=KYOCERA AVX; Manufacturer_Part_Number=08051C105K4T2A; Value=1µF; Description=CAP CER 1UF 100V X7R 0805
- Brensan_Part_Number=-; Manufacturer=Chemi-Con; Manufacturer_Part_Number=HHXE250GRA560MF61G; Value=56µF; Description=CAP ALUM HYBRID 56UF 20% 25V SMD
- Brensan_Part_Number=-; Manufacturer=Murata Electronics; Manufacturer_Part_Number=GCM1555C1H621FA16D; Value=620pF; Description=CAP CER 620PF 50V C0G/NP0 0402
- Brensan_Part_Number=-; Manufacturer=Cal-Chip Electronics, Inc.; Manufacturer_Part_Number=GMC21X5R226M25NT; Value=22µF; Description=CAP CER 22UF 25V X5R 0805
- Brensan_Part_Number=-; Manufacturer=KEMET; Manufacturer_Part_Number=C0603C152J5GACTU; Value=1500pF; Description=CAP CER 1500PF 50V C0G/NP0 0603

### Connector (18)
- Brensan_Part_Number=BR-16-105-500; Manufacturer=Amphenol ICC (Commercial Products); Manufacturer_Part_Number=MUSBRM1C130; Value=USB; Description=CONN RCPT TYPEC 24POS PCB R/A
- Brensan_Part_Number=BR-16-105-951; Manufacturer=Samtec Inc.; Manufacturer_Part_Number=HLE-108-02-L-DV-LC; Value=HLE-108-02-L-DV-LC; Description=CONN RCPT 16POS 0.1 GOLD SMD
- Brensan_Part_Number=BR-16-106-014; Manufacturer=Molex; Manufacturer_Part_Number=0522711079; Value=Jack; Description=CONN FPC BOTTOM 10POS 1MM R/A
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=0436500205; Value=0436500205; Description=CONN HEADER R/A 2POS 3MM
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=0436450310; Value=0436450310; Description=CONN RCPT HSG 3POS 3.00MM
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=5024430870; Value=5024430870; Description=CONN RCPT 8POS 0.079 TIN SMD
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=5023820870; Value=5023820870; Description=CONN RCPT 8POS 0.049 TIN SMD
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=0522711079; Value=0522711079; Description=CONN FPC BOTTOM 10POS 1MM R/A
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=5055670871; Value=5055670871; Description=CONN HEADER SMD R/A 8POS 1.25MM
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=502386-0871; Value=502386-0871; Description=1.25 W/B SGL R/A Rec Assy 8Ckt G
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=0522710479; Value=0522710479; Description=CONN FPC BOTTOM 4POS 1MM R/A
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=5023860471; Value=5023860471; Description=CONN RCPT 4P 0.049 GOLD SMD R/A
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=5023860771; Value=5023860771; Description=CONN RCPT 7P 0.049 GOLD SMD R/A
- Brensan_Part_Number=-; Manufacturer=Samtec Inc.; Manufacturer_Part_Number=TSM-108-02-L-DV-LC; Value=TSM-108-02-L-DV-LC; Description=CONN HEADER SMD 16POS 2.54MM
- Brensan_Part_Number=-; Manufacturer=GCT; Manufacturer_Part_Number=USB4145-03-0170-C; Value=USB4145-03-0170-C; Description=USB 2.0 TYPE-C RECPT VERT SMT 1.
- Brensan_Part_Number=-; Manufacturer=GCT; Manufacturer_Part_Number=USB4145-03-0170-C; Value=USB4145-03-0170-C; Description=USB 2.0 TYPE-C RECPT VERT SMT 1.
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=2125201006; Value=2125201006; Description=MINI-FIT MAX 6CKT HDR VERT
- Brensan_Part_Number=-; Manufacturer=Molex; Manufacturer_Part_Number=0526101072; Value=0526101072; Description=CONN FPC VERT 10POS 1MM SMD

### Crystal (3)
- Brensan_Part_Number=BR-16-105-446; Manufacturer=Abracon LLC; Manufacturer_Part_Number=ABS06-32.768KHZ-1-T; Value=ABS06-32.768KHZ-1-T; Description=CRYSTAL 32.7680KHZ 12.5PF SMD
- Brensan_Part_Number=-; Manufacturer=Jauch Quartz; Manufacturer_Part_Number=Q 0,032768-JTX310-12,5-20-T1-HMR-LF; Value=Q 0,032768-JTX310-12,5-20-T1-HMR-LF; Description=CRYSTAL 32.7680KHZ 12.5PF SMD
- Brensan_Part_Number=-; Manufacturer=Jauch Quartz; Manufacturer_Part_Number=Q 0,032768-JTX310-12,5-20-T1-HMR-LF; Value=Q 0,032768-JTX310-12,5-20-T1-HMR-LF; Description=CRYSTAL 32.7680KHZ 12.5PF SMD

### Diode (8)
- Brensan_Part_Number=BR-16-105-002; Manufacturer=Littelfuse Inc.; Manufacturer_Part_Number=30KPA102CA; Value=30KPA102CA; Description=TVS DIODE 102VWM 165.6VC P600
- Brensan_Part_Number=-; Manufacturer=Texas Instruments; Manufacturer_Part_Number=TPD1E05U06DYAR; Value=TPD1E05U06DYAR; Description=TVS DIODE 5.5VWM 14VC SOT5X3
- Brensan_Part_Number=-; Manufacturer=Littelfuse Inc.; Manufacturer_Part_Number=SMBJ45CA; Value=SMBJ45CA; Description=TVS DIODE 45VWM 72.7VC DO214AA
- Brensan_Part_Number=-; Manufacturer=Diodes Incorporated; Manufacturer_Part_Number=1N4448WSFQ-7; Value=1N4448WSFQ-7; Description=DIODE STANDARD 75V 500MA SOD323F
- Brensan_Part_Number=-; Manufacturer=Rohm Semiconductor; Manufacturer_Part_Number=RF071MM2STR; Value=RF071MM2STR; Description=DIODE STANDARD 200V 700MA PMDU
- Brensan_Part_Number=-; Manufacturer=Littelfuse Inc.; Manufacturer_Part_Number=SMBJ48CA; Value=SMBJ48CA; Description=TVS DIODE 48VWM 77.4VC DO214AA
- Brensan_Part_Number=-; Manufacturer=Central Semiconductor Corp; Manufacturer_Part_Number=CMHZ5264B TR PBFREE; Value=CMHZ5264B TR PBFREE; Description=DIODE ZENER 60V 500MW SOD123
- Brensan_Part_Number=-; Manufacturer=Comchip Technology; Manufacturer_Part_Number=ATV02W120B-HF; Value=ATV02W120B-HF; Description=TVS DIODE 12VWM 19.9VC SOD123F

### Header (2)
- Value=Header 01x09; Description=HEADER 01x09 2.54mm PITCH
- Value=Header 01x10; Description=HEADER 01x10 2.54mm PITCH

### IC (5)
- Brensan_Part_Number=-; Manufacturer=Texas Instruments; Manufacturer_Part_Number=BQ32000DR; Value=BQ32000DR; Description=IC RTC CLK/CALENDAR I2C 8SOIC
- Brensan_Part_Number=BR-16-105-737; Manufacturer=Diodes Incorporated; Manufacturer_Part_Number=AP7375Q-33SP-13; Value=AP7375Q-33SP-13; Description=IC REG LINEAR 3.3V 350MA 8-SO-EP
- Brensan_Part_Number=-; Manufacturer=Texas Instruments; Manufacturer_Part_Number=THVD1420DRLR; Value=THVD1420DRLR; Description=IC TRANSCEIVER HALF 1/1 SOT583
- Brensan_Part_Number=-; Manufacturer=Texas Instruments; Manufacturer_Part_Number=TLV9062IDR; Value=TLV9062IDR; Description=IC CMOS 2 CIRCUIT 8SOIC
- Brensan_Part_Number=-; Manufacturer=Texas Instruments; Manufacturer_Part_Number=TPS259482AYWPR; Value=TPS259482AYWPR; Description=3.5-V TO 23-V, 12.2-M, 8-A EFUSE

### Inductor (4)
- Brensan_Part_Number=-; Manufacturer=KEMET; Manufacturer_Part_Number=SCR39XV-310-1R9B007JH; Value=CMC 640UH 31A 2LN TH AEC-Q200; Description=CMC 640UH 31A 2LN TH AEC-Q200
- Brensan_Part_Number=-; Manufacturer=Abracon LLC; Manufacturer_Part_Number=ACML-0805-500-T; Value=ACML-0805-500-T; Description=FERRITE BEAD 50 OHM 0805 1LN
- Brensan_Part_Number=-; Manufacturer=Pulse Electronics; Manufacturer_Part_Number=PG1096.252NLT; Value=2.25 µH; Description=FIXED IND 2.25UH 45A 1.2 MOHM
- Brensan_Part_Number=-; Manufacturer=Laird-Signal Integrity Products; Manufacturer_Part_Number=CMX1616Z171B-10; Value=CMC 176UH 62A 2LN TH; Description=CMC 176UH 62A 2LN TH

### MCU (1)
- Brensan_Part_Number=-; Manufacturer=STMicroelectronics; Manufacturer_Part_Number=STM32F412RET6; Value=STM32F412RET6; Description=IC MCU 32BIT 512KB FLASH 64LQFP

### Mosfet (6)
- Brensan_Part_Number=-; Manufacturer=Nexperia USA Inc.; Manufacturer_Part_Number=PSMN1R7-60BS,118; Value=PSMN1R7-60BS,118; Description=MOSFET N-CH 60V 120A D2PAK
- Brensan_Part_Number=-; Manufacturer=Infineon Technologies; Manufacturer_Part_Number=IAUS300N08S5N012ATMA1; Value=IAUS300N08S5N012ATMA1; Description=MOSFET N-CH 80V 300A HSOG-8
- Brensan_Part_Number=-; Manufacturer=Infineon Technologies; Manufacturer_Part_Number=IAUTN08S7N007ATMA1; Value=IAUTN08S7N007ATMA1; Description=OPTIMOSTM 7 TOLL AUTOMOTIVE POWE
- Brensan_Part_Number=-; Manufacturer=STMicroelectronics; Manufacturer_Part_Number=STL225N6F7AG; Value=MOSFET N-CH 60V 120A POWERFLAT; Description=MOSFET N-CH 60V 120A POWERFLAT
- Brensan_Part_Number=-; Manufacturer=Toshiba Semiconductor and Storage; Manufacturer_Part_Number=TPH8R008NH,L1Q; Value=TPH8R008NH,L1Q; Description=MOSFET N-CH 80V 34A 8SOP
- Brensan_Part_Number=-; Manufacturer=Vishay Siliconix; Manufacturer_Part_Number=SUD50P06-15L-E3; Value=SUD50P06-15L-E3; Description=MOSFET P-CH 60V 50A TO252

### Resistor (14)
- Brensan_Part_Number=BR-16-105-019; Manufacturer=YAGEO; Manufacturer_Part_Number=RC0603JR-07150KL; Value=150KΩ; Description=RES 150K OHM 5% 1/10W 0603
- Brensan_Part_Number=BR-16-105-578; Manufacturer=YAGEO; Manufacturer_Part_Number=RC0603FR-07133KL; Value=133KΩ; Description=RES 133K OHM 1% 1/10W 0603
- Brensan_Part_Number=-; Manufacturer=YAGEO; Manufacturer_Part_Number=RC0603JR-07180KL; Value=180KΩ; Description=RES 180K OHM 5% 1/10W 0603
- Brensan_Part_Number=-; Manufacturer=Stackpole Electronics Inc; Manufacturer_Part_Number=CSS2725FT1L00; Value=1 mΩ; Description=RES 0.001 OHM 1% 4W 2725
- Brensan_Part_Number=-; Manufacturer=KOA Speer Electronics, Inc.; Manufacturer_Part_Number=RN73H1JTTD5422F100; Value=54.2KΩ; Description=RES 54.2K OHM 1% 1/10W 0603
- Brensan_Part_Number=-; Manufacturer=Vishay Dale; Manufacturer_Part_Number=CRCW060326K1FKEA; Value=26.1KΩ; Description=RES SMD 26.1K OHM 1% 1/8W 0603
- Brensan_Part_Number=-; Manufacturer=Stackpole Electronics Inc; Manufacturer_Part_Number=RNCF0603BTC328K; Value=328KΩ; Description=RES SMD 328K OHM 0.1% 1/6W 0603
- Brensan_Part_Number=-; Manufacturer=YAGEO; Manufacturer_Part_Number=RT0603FRE075K23L; Value=5.23KΩ; Description=RES SMD 5.23K OHM 1% 1/10W 0603
- Brensan_Part_Number=-; Manufacturer=KOA Speer Electronics, Inc.; Manufacturer_Part_Number=TLR2HWDTE1L00F50; Value=1 mΩ; Description=RES 0.001 OHM 1% 2W 2010
- Brensan_Part_Number=-; Manufacturer=Stackpole Electronics Inc; Manufacturer_Part_Number=RMCF0603FT47R0; Value=47Ω; Description=RES 47 OHM 1% 1/10W 0603
- Brensan_Part_Number=-; Manufacturer=Bourns Inc.; Manufacturer_Part_Number=CR0603-FX-1203ELF; Value=120KΩ; Description=RES SMD 120K OHM 1% 1/10W 0603
- Brensan_Part_Number=-; Manufacturer=Vishay Dale; Manufacturer_Part_Number=CRCW20100000Z0EFHP; Value=0Ω; Description=RES SMD 0 OHM JUMPER 1W 2010
- Brensan_Part_Number=-; Manufacturer=Vishay Dale; Manufacturer_Part_Number=CRCW20100000Z0EFHP; Value=0Ω; Description=RES SMD 0 OHM JUMPER 1W 2010
- Brensan_Part_Number=-; Manufacturer=Riedon products by Bourns; Manufacturer_Part_Number=PF2203-24RF1; Value=24Ω; Description=RES 24 OHM 1% 35W TO220

### Transistor (2)
- Brensan_Part_Number=-; Manufacturer=onsemi; Manufacturer_Part_Number=MMBTA56LT1G; Value=MMBTA56LT1G; Description=TRANS PNP 80V 0.5A SOT23-3
- Brensan_Part_Number=-; Manufacturer=onsemi; Manufacturer_Part_Number=MMBTA56LT1G; Value=MMBTA56LT1G; Description=TRANS PNP 80V 0.5A SOT23-3
