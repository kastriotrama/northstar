"""Which wheels a model drives, reviewed model by model.

The registry says whether a car is four-wheel drive, and it is almost never
wrong about that. For a car it marks as *not* four-wheel drive, the driven axle
is a property of the model: a Golf is front-wheel drive, a 3 Series rear-wheel
drive. This table states that layout per make and model -- with a year range
where a model changed layout between generations, and a fuel where the electric
version differs from the combustion one -- so the drive type can be filled for
exactly those cars.

Reviewed knowledge, like the model names in `vehicle_model_patterns`: every
entry is a statement about real cars, not something learned from the data. The
rule is to leave out what is not certain. A model sold with both layouts in the
same years (vans, the BMW 2 Series) has no entry, and neither has a make
without a model unless every two-wheel-drive car of that make and era drives the
same axle.

Model names are the `model_family` values the vehicles carry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Layout = Literal["fwd", "rwd"]

ELECTRIC_FUEL = "electricity"


@dataclass(frozen=True)
class Era:
    """One layout of a model: for all its years, or for a range of build years."""

    layout: Layout
    year_from: int | None = None
    year_to: int | None = None
    #: True: only the electric version. False: every other version. None: all.
    electric: bool | None = None

    def covers(self, year: int | None, fuel: str | None) -> bool:
        if self.electric is not None and (fuel == ELECTRIC_FUEL) != self.electric:
            return False
        if self.year_from is None and self.year_to is None:
            return True
        if year is None:
            return False
        return (self.year_from is None or year >= self.year_from) and (
            self.year_to is None or year <= self.year_to
        )


def _until(year: int, layout: Layout) -> Era:
    return Era(layout, None, year)


def _since(year: int, layout: Layout) -> Era:
    return Era(layout, year, None)


def _between(first: int, last: int, layout: Layout) -> Era:
    return Era(layout, first, last)


#: Models with one layout through all their years: make -> layout -> model families.
_ONE_LAYOUT: dict[str, dict[Layout, tuple[str, ...]]] = {
    "Volvo": {
        "fwd": ("V70", "V50", "S60", "S80", "S40", "850", "S70", "V40", "C30", "C70",
                "V60", "XC90", "460"),
        # Every Volvo up to the 900 series drove the rear wheels, the 340 included. The
        # single-motor EX30 does too; its cars carry the family name "EX30 Cross Country"
        # whether or not they are one, and the real Cross Country is four-wheel drive.
        "rwd": ("240", "940", "740", "760", "780", "960", "140", "164", "Amazon", "544",
                "Pv 444", "Pv 544", "Duett", "P 1800", "340", "EX40", "EC40", "EX30",
                "EX30 Cross Country", "ES90"),
    },
    "Volkswagen": {
        "fwd": ("Golf", "Passat", "Polo", "Sharan", "Up!", "Taigo", "T-roc", "Jetta", "Tiguan",
                "Caddy", "Touran", "New Beetle", "T-cross", "Arteon", "Lupo", "Tayron",
                "Vento", "Scirocco", "Bora", "Eos", "Fox", "Corrado", "K70",
                ),
        # The electric ID. models drive the rear axle; so did the rear-engined Karmann Ghia.
        "rwd": ("ID.3", "ID.4", "ID.5", "ID.7", "ID.7 Tourer", "ID. Buzz", "Karmann Ghia"),
    },
    "Mercedes-Benz": {
        "fwd": ("A-Class", "B-Class", "GLA", "EQA", "EQB", "T-Class", "Citan"),
        "rwd": ("C-Class", "E-Class", "SLK", "SL", "CLK", "S-Class", "CLS", "CLC", "AMG GT",
                "SLC", "EQE", "EQS", "CLE", "Viano"),
    },
    "Ford": {
        "fwd": ("Focus", "Mondeo", "Fiesta", "S-max", "C-max", "Ka", "Puma", "Fusion", "Kuga",
                "Ecosport", "Grand C-max", "B-max", "Tourneo Connect", "Tourneo Courier",
                "Street Ka", "Probe", "Windstar"),
        "rwd": ("Mustang", "Mustang Mach-e", "Sierra", "Scorpio", "Thunderbird", "Granada",
                "Fairlane", "Capri", "Galaxie 500", "Galaxie", "Cortina", "Anglia", "Consul",
                "Crown Victoria", "Falcon", "Customline", "Torino", "Ltd", "Zephyr", "Skyliner",
                "Model A", "Country Sedan", "Taunus 17M"),
    },
    "Toyota": {
        "fwd": ("Yaris", "Avensis", "Prius", "Aygo", "Camry", "Auris", "Yaris Cross", "C-hr",
                "Corolla Verso", "Prius Plus", "Iq", "Urban Cruiser", "Prius Phv", "Aygo X",
                "Picnic", "bZ4X", "RAV4", "Proace"),
        "rwd": ("Supra", "Hiace", "MR2"),
    },
    "BMW": {
        "rwd": ("3 Series", "5 Series", "7 Series", "Z4", "Z3", "i3", "4 Series", "6 Series",
                "8 Series"),
    },
    "Peugeot": {
        "fwd": ("307", "206", "207", "5008", "508", "107", "3008", "407", "2008", "308", "406",
                "205", "Partner", "306", "108", "405", "408", "208", "Rifter", "607", "Expert",
                "605", "Rcz", "309", "807", "1007", "106", "Traveller", "806"),
        "rwd": ("504",),
    },
    "Opel": {
        "fwd": ("Astra", "Corsa", "Vectra", "Mokka", "Insignia", "Zafira", "Meriva",
                "Grandland X", "Crossland X", "Agila", "Vivaro", "Tigra", "Combo", "Mokka X",
                "Grandland", "Calibra", "Karl", "Ampera", "Sintra", "Adam"),
        "rwd": ("Rekord", "Omega", "Manta", "Commodore", "Kapitän", "Gt"),
    },
    "Škoda": {
        "fwd": ("Fabia", "Kamiq", "Kodiaq", "Karoq", "Yeti", "Scala", "Roomster", "Citigo"),
        "rwd": ("Enyaq", "Elroq"),
    },
    "Kia": {
        "fwd": ("Ceed", "EV3", "Stonic", "Soul", "Niro", "Carens", "Picanto", "EV5",
                "Rio", "EV4", "Proceed", "Xceed", "Carnival", "Cerato", "Magentis", "Pride",
                "PV5"),
        "rwd": ("EV6", "EV9"),
    },
    "Renault": {
        "fwd": ("Clio", "Megane", "Kadjar", "Laguna", "5", "Kangoo", "Scenic E-Tech", "Espace",
                "Talisman", "Modus", "Arkana", "Megane Scenic", "Austral", "Captur", "Scenic",
                "12", "16"),
        "rwd": ("10",),
    },
    "Audi": {
        "fwd": ("A3", "A4", "100", "Q3", "Q2", "80", "TT", "A2", "Coupe", "Cabriolet",
                "A1", "A5"),
        # The electric Q4 and Q6 e-tron drive the rear axle when they are not quattro.
        "rwd": ("Q4", "Q6"),
    },
    "Nissan": {
        "fwd": ("Micra", "Leaf", "Juke", "Note", "Almera", "Qashqai", "Primera", "X-trail",
                "Pulsar", "Ariya", "NV200", "Primastar", "NV300"),
        "rwd": ("200SX", "350Z", "Skyline", "300ZX"),
    },
    "Mazda": {
        "fwd": ("6", "Mazda3", "CX-3", "2", "3", "CX-30", "MX-30", "CX-5", "5", "Demio",
                "Premacy"),
        "rwd": ("MX-5", "RX-7", "RX-8", "6e"),
    },
    "Citroën": {
        "fwd": ("C5", "C3", "Berlingo", "C1", "Xsara", "Xantia", "2 Cv", "Cx", "Xm",
                "C3 Aircross", "C4 Picasso", "BX", "C2", "DS3", "C8", "Ds", "Id", "DS4", "DS5",
                "ZX", "Jumper", "C6", "Jumpy", "Spacetourer", "Evasion"),
    },
    "Hyundai": {
        "fwd": ("Kona", "i40", "Getz", "ix20", "Atos", "Tucson", "Ioniq", "Accent", "i30", "i10",
                "ix35", "Elantra", "Bayon", "Matrix", "Sonata", "Trajet", "Coupe", "i20",
                "Veloster", "Inster"),
        "rwd": ("Ioniq 5", "Genesis", "H-1"),
    },
    "Chevrolet": {
        "fwd": ("Cruze", "Aveo", "Trans Sport", "Spark", "Matiz", "Orlando", "Nubira",
                "Uplander", "Trax", "Epica", "Captiva", "Alero", "Lacetti", "Tacuma"),
        "rwd": ("Corvette", "Camaro", "Bel Air", "Caprice", "Chevelle", "Chevy", "210",
                "Biscayne", "Master", "Corvair", "Monza", "Astro", "Express"),
    },
    "Mitsubishi": {
        "fwd": ("Space Star", "Asx", "Carisma", "Grandis", "Space Wagon",
                "Eclipse", "Outlander"),
    },
    "Honda": {
        "fwd": ("Civic", "Jazz", "Accord", "Hr-v", "Cr-v", "e:Ny1", "Prelude", "Insight", "Fr-v",
                "Zr-v", "Legend"),
        "rwd": ("e", "S2000"),
    },
    "SEAT": {
        "fwd": ("Arona", "Ibiza", "Leon", "Alhambra", "Ateca", "Toledo", "Cordoba", "Altea Xl",
                "Tarraco", "Arosa", "Mii", "Altea", "Exeo St", "Formentor"),
        "rwd": ("Born",),
    },
    "Fiat": {
        "fwd": ("Tipo", "Punto", "Panda", "Uno", "Barchetta", "Ducato", "128", "Bravo", "127",
                "Stilo", "Grande Punto", "Freemont", "Croma", "Brava", "Doblo", "Coupe", "Marea"),
        "rwd": ("850", "124", "126", "1100", "X1/9", "131", "1500", "124 Spider"),
    },
    "MINI": {"fwd": ("MINI", "Mini", "Mini Clubman")},
    "MG": {
        "fwd": ("Zs", "MG5", "EHS", "Hs", "MG3"),
        "rwd": ("Marvel R", "Mgb", "Mga", "Tf", "Mgf", "Midget", "Mgb Gt", "Td"),
    },
    "Chrysler": {
        "fwd": ("Grand Voyager", "Pt Cruiser", "Voyager", "Sebring", "Stratus",
                "300M", "Neon", "Pacifica", "Vision"),
        "rwd": ("300C", "Newport", "Windsor", "Crossfire"),
    },
    "Porsche": {
        "rwd": ("911", "Boxster", "944", "718", "924", "928", "Cayman", "Taycan", "356", "912",
                "Panamera", "Carrera", "914"),
    },
    "Suzuki": {"fwd": ("Swift", "SX4", "Alto", "Baleno", "Splash", "Swace", "Liana", "Ignis")},
    "Jaguar": {
        "fwd": ("X-Type",),
        "rwd": ("Xf", "Xj", "S-type", "Sovereign", "XJ6", "XKR", "XK8", "Xe", "Xk", "XJS", "XJR",
                "E-type", "F-type", "XJ8"),
    },
    "Cadillac": {"fwd": ("Bls",), "rwd": ("Series 62", "Cts")},
    "Pontiac": {
        "fwd": ("Trans Sport",),
        "rwd": ("Firebird", "Catalina", "Gto", "Star Chief", "Fiero", "Chieftain",
                "Ventura"),
    },
    "Dacia": {"fwd": ("Logan", "Sandero", "Jogger", "Duster", "Lodgy", "Bigster")},
    "Lexus": {"fwd": ("ES", "UX", "CT", "LBX", "NX"), "rwd": ("IS", "GS", "LS", "SC")},
    "Tesla": {"rwd": ("Model 3", "Model Y", "Model S")},
    "Alfa Romeo": {
        "fwd": ("156", "159", "147", "Mito", "164", "166"),
        "rwd": ("Giulia", "75", "Alfetta"),
    },
    "Dodge": {
        "fwd": ("Caliber", "Grand Caravan", "Journey"),
        "rwd": ("Challenger", "Coronet", "Polara", "Magnum", "Viper", "Custom Royal"),
    },
    "Buick": {"rwd": ("Roadmaster", "Special", "Wildcat", "Super")},
    "Polestar": {"rwd": ("4",)},
    "Oldsmobile": {"rwd": ("Super 88", "Dynamic 88")},
    "Lynk & Co": {"fwd": ("01", "08"), "rwd": ("02",)},
    "Plymouth": {
        "rwd": ("Valiant", "Barracuda", "Fury", "Satellite", "Road Runner", "Belvedere", "GTX",
                "Volare"),
    },
    "Cupra": {"fwd": ("Leon", "Formentor", "Terramar", "Ateca"), "rwd": ("Born",)},
    "BYD": {"fwd": ("Atto 3", "Seal U")},
    "Austin": {"fwd": ("Mini", "Allegro"), "rwd": ("A30", "A40")},
    "BMC": {"fwd": ("Mini", "1100", "1300")},
    "Smart": {"rwd": ("Fortwo", "City-coupe", "Roadster", "#1", "#3", "#5")},
    "Triumph": {"rwd": ("Spitfire", "Herald", "TR6")},
    "Lincoln": {"rwd": ("Town Car",)},
    "Lancia": {"fwd": ("Voyager", "Ypsilon")},
    "Mercury": {"rwd": ("Montclair",)},
    "Morris": {"rwd": ("Minor",)},
    "Morgan": {"rwd": ("4/4", "Plus 4", "Plus 8")},
    "Jeep": {"rwd": ("Grand Cherokee",)},
    "ORA": {"fwd": ("Funky Cat",)},
    "Ferrari": {"rwd": ("California", "F430")},
    "Lotus": {"rwd": ("Elise",)},
    "Maserati": {"rwd": ("Quattroporte",)},
    "Aston Martin": {"rwd": ("DB9", "V8 Vantage")},
    "Daewoo": {"fwd": ("Kalos", "Matiz")},
    "DS": {"fwd": ("Ds 7 Crossback", "Ds 3", "Ds 4")},
    "DeSoto": {"rwd": ("Firedome",)},
    "Subaru": {"fwd": ("Justy",), "rwd": ("Brz",)},
    "Lada": {"fwd": ("Samara",)},
    "Maxus": {"fwd": ("Euniq 6",)},
    "Edsel": {"rwd": ("Pacer",)},
    "Aiways": {"fwd": ("U5",)},
    "XPENG": {"rwd": ("G9",)},
    "Saab": {"fwd": ("9-3", "9-5", "900", "9000", "99", "96", "95")},
}

#: Models whose layout depends on the build year (a new generation moved the driven
#: axle) or on the fuel. A year no era covers is left alone.
_BY_ERA: dict[tuple[str, str], tuple[Era, ...]] = {
    # The 1997-98 S90 and V90 were the renamed 960; the names came back in 2016 on a
    # front-wheel-drive platform.
    ("Volvo", "S90"): (_until(1998, "rwd"), _since(2016, "fwd")),
    ("Volvo", "V90"): (_until(1998, "rwd"), _since(2016, "fwd")),
    # The 440 family is the 1988 hatchback; older cars under the name are PV 444s.
    # Before the 440 of 1988 the name is the registry's for the PV 444 and its estate.
    ("Volvo", "440"): (_until(1970, "rwd"), _since(1987, "fwd")),
    # Combustion XC40s drive the front wheels throughout. The electric one moved
    # from front- to rear-wheel drive with model year 2024, built from mid-2023.
    ("Volvo", "XC40"): (
        Era("fwd", electric=False),
        Era("fwd", None, 2022, electric=True),
        Era("rwd", 2024, None, electric=True),
    ),
    ("Volvo", "C40"): (_until(2022, "fwd"), _since(2024, "rwd")),
    # The classic Beetle was rear-engined; the family's Beetle is the 1998 and 2011 cars.
    ("Volkswagen", "Beetle"): (_since(1997, "fwd"),),
    # The electric A6 e-tron and the electric CLA of 2025 drive the rear axle.
    ("Audi", "A6"): (Era("fwd", electric=False), Era("rwd", electric=True)),
    ("Mercedes-Benz", "CLA"): (Era("fwd", electric=False), Era("rwd", 2025, None, electric=True)),
    # The electric Tourneo Custom has its motor at the rear axle.
    ("Ford", "Tourneo Custom"): (Era("fwd", electric=False), Era("rwd", electric=True)),
    # Rapid, Felicia and Superb are old Škoda names reused on front-wheel-drive cars.
    ("Škoda", "Rapid"): (_since(2012, "fwd"),),
    ("Škoda", "Felicia"): (_since(1994, "fwd"),),
    ("Škoda", "Superb"): (_since(2001, "fwd"),),
    # The rear-engined T3 gave way to the front-wheel-drive T4 during 1990.
    ("Volkswagen", "Caravelle"): (_until(1989, "rwd"), _since(1991, "fwd")),
    ("Volkswagen", "Multivan"): (_until(1989, "rwd"), _since(1991, "fwd")),
    # The first Crafter was a Sprinter; the second comes with either axle driven.
    ("Volkswagen", "Crafter"): (_until(2016, "rwd"),),
    # W638 was front-wheel drive, W639 rear; from W447 a Vito can be either.
    ("Mercedes-Benz", "V-Class"): (_until(2005, "fwd"), _since(2014, "rwd")),
    ("Mercedes-Benz", "Vito"): (_until(2002, "fwd"), _between(2004, 2013, "rwd")),
    ("Mercedes-Benz", "Sprinter"): (_until(2017, "rwd"),),
    # Escort Mk1 and Mk2 drove the rear wheels; the 1980 Mk3 the front.
    ("Ford", "Escort"): (_until(1979, "rwd"), _since(1981, "fwd")),
    # The front-wheel-drive 12M and 15M shared the Taunus name through the 1960s.
    ("Ford", "Taunus"): (_until(1961, "rwd"), _since(1971, "rwd")),
    ("Ford", "Galaxy"): (_since(1995, "fwd"),),
    ("Ford", "Cougar"): (_since(1998, "fwd"),),
    ("Ford", "Explorer"): (_until(2010, "rwd"), _since(2020, "rwd")),
    # Corolla, Starlet, Celica and Carina each moved to front-wheel drive in the mid-1980s.
    ("Toyota", "Corolla"): (_until(1982, "rwd"), _since(1988, "fwd")),
    ("Toyota", "Starlet"): (_until(1983, "rwd"), _since(1985, "fwd")),
    ("Toyota", "Celica"): (_until(1984, "rwd"), _since(1986, "fwd")),
    ("Toyota", "Carina"): (_until(1982, "rwd"), _since(1985, "fwd")),
    # The first Previa had its engine under the floor and drove the rear wheels.
    ("Toyota", "Previa"): (_until(1999, "rwd"), _since(2000, "fwd")),
    # The 1 Series and the X1 moved to the front-wheel-drive platform (F40, F48).
    ("BMW", "1 Series"): (_until(2018, "rwd"), _since(2020, "fwd")),
    ("BMW", "X1"): (_until(2014, "rwd"), _since(2016, "fwd")),
    # Kadett A to C and Ascona A and B were rear-wheel drive.
    ("Opel", "Kadett"): (_until(1978, "rwd"), _since(1980, "fwd")),
    ("Opel", "Ascona"): (_until(1980, "rwd"), _since(1982, "fwd")),
    ("Opel", "Movano"): (_until(2009, "fwd"),),
    # The second Master drove the front wheels only; its successor came with either axle.
    ("Renault", "Master"): (_until(2009, "fwd"),),
    # The electric GLB of 2026 drives the rear axle, like the electric CLA.
    ("Mercedes-Benz", "GLB"): (Era("fwd", electric=False), Era("rwd", 2025, None, electric=True)),
    # The MG4 Urban of 2026 drives the front wheels; it is told apart by its power.
    ("MG", "MG4"): (_until(2025, "rwd"),),
    # The 1960s Octavia was rear-wheel drive; the name returned in 1996.
    ("Škoda", "Octavia"): (_until(1971, "rwd"), _since(1996, "fwd")),
    # The 4CV was rear-engined; the Renault 4 that followed drove the front wheels.
    ("Renault", "4"): (_until(1961, "rwd"), _since(1962, "fwd")),
    # The third Twingo shares the Smart's rear-engine layout.
    ("Renault", "Twingo"): (_until(2013, "fwd"), _since(2015, "rwd")),
    ("Renault", "Trafic"): (_since(2001, "fwd"),),
    ("Mazda", "323"): (_until(1979, "rwd"), _since(1981, "fwd")),
    ("Mazda", "626"): (_until(1981, "rwd"), _since(1983, "fwd")),
    # The pre-war C4 was rear-wheel drive; the model families' C4 is the 2004 car.
    ("Citroën", "C4"): (_since(2004, "fwd"),),
    ("Mitsubishi", "Lancer"): (_since(1988, "fwd"),),
    ("Chevrolet", "Impala"): (_until(1996, "rwd"), _since(2000, "fwd")),
    ("Chevrolet", "Malibu"): (_until(1983, "rwd"), _since(1997, "fwd")),
    ("Chevrolet", "Monte Carlo"): (_until(1988, "rwd"), _since(1995, "fwd")),
    # The Nuova 500 and the 600 were rear-engined; their modern namesakes are not.
    ("Fiat", "500"): (_until(1977, "rwd"), _since(2007, "fwd")),
    ("Fiat", "600"): (_until(1970, "rwd"), _since(2023, "fwd")),
    ("Chrysler", "New Yorker"): (_until(1981, "rwd"), _since(1983, "fwd")),
    ("Chrysler", "Imperial"): (_until(1983, "rwd"), _since(1990, "fwd")),
    ("Suzuki", "Vitara"): (_since(2015, "fwd"),),
    ("Cadillac", "Deville"): (_until(1984, "rwd"), _since(1985, "fwd")),
    ("Cadillac", "Eldorado"): (_until(1966, "rwd"), _since(1967, "fwd")),
    ("Cadillac", "Fleetwood"): (_until(1984, "rwd"), _since(1993, "rwd")),
    ("Cadillac", "Seville"): (_until(1979, "rwd"), _since(1980, "fwd")),
    ("Cadillac", "Sts"): (_until(2004, "fwd"), _since(2005, "rwd")),
    ("Pontiac", "Bonneville"): (_until(1986, "rwd"), _since(1987, "fwd")),
    ("Pontiac", "Lemans"): (_until(1981, "rwd"), _since(1988, "fwd")),
    ("Pontiac", "Grand Prix"): (_until(1987, "rwd"), _since(1988, "fwd")),
    ("Alfa Romeo", "Giulietta"): (_until(1985, "rwd"), _since(2010, "fwd")),
    ("Alfa Romeo", "Spider"): (_until(1993, "rwd"), _since(1995, "fwd")),
    ("Alfa Romeo", "Gtv"): (_until(1987, "rwd"), _since(1995, "fwd")),
    ("Alfa Romeo", "Gt"): (_until(1977, "rwd"), _since(2003, "fwd")),
    ("Dodge", "Charger"): (_until(1978, "rwd"), _since(2005, "rwd")),
    ("Buick", "Electra"): (_until(1984, "rwd"),),
    ("Buick", "Skylark"): (_until(1979, "rwd"), _since(1980, "fwd")),
    ("Buick", "Lesabre"): (_until(1985, "rwd"), _since(1986, "fwd")),
    ("Buick", "Riviera"): (_until(1978, "rwd"), _since(1979, "fwd")),
    ("Buick", "Century"): (_until(1981, "rwd"), _since(1982, "fwd")),
    ("Buick", "Regal"): (_until(1987, "rwd"), _since(1988, "fwd")),
    # The single-motor Polestar 2 moved to rear-wheel drive with model year 2024.
    ("Polestar", "2"): (_until(2022, "fwd"), _since(2024, "rwd")),
    ("Oldsmobile", "Cutlass"): (_until(1981, "rwd"),),
    ("Oldsmobile", "98"): (_until(1984, "rwd"), _since(1985, "fwd")),
    ("Oldsmobile", "Ninety-Eight"): (_until(1984, "rwd"), _since(1985, "fwd")),
    # The P4 "75" of the 1950s, and the front-wheel-drive saloon of 1999.
    ("Rover", "75"): (_until(1960, "rwd"), _since(1999, "fwd")),
    ("Smart", "Forfour"): (_until(2006, "fwd"), _since(2014, "rwd")),
    ("Lincoln", "Continental"): (_until(1987, "rwd"), _since(1988, "fwd")),
    # The 1980s Thema drove the front wheels; the 2011 one was a Chrysler 300.
    ("Lancia", "Thema"): (_until(1994, "fwd"), _since(2011, "rwd")),
    ("Mercury", "Cougar"): (_until(1997, "rwd"),),
    # Models that were four-wheel drive only, or drove the other axle, in some years.
    # Four-wheel drive only until the front-driven versions of 2009.
    ("Volvo", "XC70"): (_since(2009, "fwd"),),
    ("Volvo", "XC60"): (_since(2009, "fwd"),),
    # The engine moved from the back to the front with the T4 of 1990; the T3 ran on until 1992.
    ("Volkswagen", "Transporter"): (_until(1989, "rwd"), _since(1993, "fwd")),
    ("Volkswagen", "California"): (_since(1993, "fwd"),),
    # Four-wheel drive only until the sDrive versions of 2009.
    ("BMW", "X3"): (_since(2009, "rwd"),),
    # The first Sportage drove the rear wheels when it was not four-wheel drive.
    ("Kia", "Sportage"): (_since(2005, "fwd"),),
    # Rear-wheel drive until the B11 of 1981; the Maxima until 1984.
    ("Nissan", "Sunny"): (_since(1983, "fwd"),),
    ("Nissan", "Maxima"): (_since(1985, "fwd"),),
    # The Nova of 1985 was a front-driven Toyota.
    ("Chevrolet", "Nova"): (_until(1979, "rwd"),),
    # The first Colts and Galants drove the rear wheels.
    ("Mitsubishi", "Colt"): (_since(1980, "fwd"),),
    ("Mitsubishi", "Galant"): (_until(1982, "rwd"), _since(1985, "fwd")),
    # Front-wheel drive from the K-cars of 1982.
    ("Chrysler", "Le Baron"): (_until(1980, "rwd"), _since(1982, "fwd")),
    ("Chrysler", "Town & Country"): (_since(1982, "fwd"),),
    # The petrol Macan is four-wheel drive only; the electric one also drives the rear axle alone.
    ("Porsche", "Macan"): (Era("rwd", electric=True),),
    # The Tempest of 1987 was front-wheel drive.
    ("Pontiac", "Tempest"): (_until(1970, "rwd"),),
    ("Dodge", "Dart"): (_until(1976, "rwd"), _since(2012, "fwd")),
    # The 88 moved to front-wheel drive for 1986.
    ("Oldsmobile", "Delta 88"): (_until(1984, "rwd"), _since(1986, "fwd")),
    ("Oldsmobile", "88"): (_until(1984, "rwd"), _since(1986, "fwd")),
    ("Oldsmobile", "442"): (_until(1987, "rwd"),),
    # The Monterey of 2004 was a front-driven minivan.
    ("Mercury", "Monterey"): (_until(1974, "rwd"),),
    # The Cherokee of 2014 drives the front wheels when it is not four-wheel drive.
    ("Jeep", "Renegade"): (_since(2014, "fwd"),),
    ("Jeep", "Cherokee"): (_until(2012, "rwd"), _since(2014, "fwd")),
}

#: Cars without a model family, where every two-wheel-drive car of the make in
#: those years drives the same axle.
_MAKE_WITHOUT_MODEL: dict[str, tuple[Era, ...]] = {
    # Before the Vito of 1996 and the A-Class of 1997 every Mercedes was rear-wheel drive.
    "Mercedes-Benz": (_until(1995, "rwd"),),
    # Until the 2 Series Active Tourer of 2014.
    "BMW": (_until(2013, "rwd"),),
    "Porsche": (Era("rwd"),),
    "Jaguar": (_until(2000, "rwd"),),
    # Until the rear-driven e-tron models.
    "Audi": (_until(2018, "fwd"),),
    "Saab": (Era("fwd"),),
    "MINI": (Era("fwd"),),
    # The 204 of 1965 was the first front-driven Peugeot. Combustion only from 1990:
    # the electric iOn and C-Zero drive the rear wheels.
    "Peugeot": (
        _until(1964, "rwd"),
        Era("fwd", 1990, None, electric=False),
        Era("fwd", 2021, None, electric=True),
    ),
    # Every Citroën since the war drives the front wheels, the C-Zero apart.
    "Citroën": (Era("fwd", 1946, None, electric=False), Era("fwd", 2021, None, electric=True)),
    # After the SD1 every Rover drove the front wheels.
    "Rover": (_until(1983, "rwd"), _since(1987, "fwd")),
    # Until the Favorit of 1988 and the Samara of 1984.
    "Škoda": (_until(1987, "rwd"),),
    "VAZ": (_until(1983, "rwd"),),
    "Mini": (Era("fwd"),),
    "Daewoo": (Era("fwd"),),
    "Lancia": (_until(1959, "rwd"), _between(1985, 2010, "fwd")),
    "Iveco": (Era("rwd"),),
    "Scania": (Era("rwd"),),
    # Until the Acadia of 2007.
    "GMC": (_until(2005, "rwd"),),
    # After the 126 and before the 124 Spider of 2016; the 500e is front-driven too.
    "Fiat": (_between(1993, 2015, "fwd"), _since(2020, "fwd")),
    # Every Volkswagen had its engine in the back until the K70 of 1970.
    "Volkswagen": (_until(1969, "rwd"),),
    # Until the 480 of 1986.
    "Volvo": (_until(1985, "rwd"),),
    # Until the Taunus 12M of 1962.
    "Ford": (_until(1961, "rwd"),),
    # Until the Kadett D of 1979 and the Astra that followed it.
    "Opel": (_until(1978, "rwd"),),
    "Vauxhall": (_until(1978, "rwd"),),
    # Until the Tercel of 1978.
    "Toyota": (_until(1977, "rwd"),),
    # Between the front-driven R130 coupe and the 323 of 1980.
    "Mazda": (_between(1973, 1979, "rwd"),),
    # Until the Alfasud of 1971.
    "Alfa Romeo": (_until(1970, "rwd"),),
    # Until the Mini of 1959, and the front-driven 200 of 1984.
    "Austin": (_until(1958, "rwd"),),
    "Morris": (_until(1958, "rwd"),),
    # Until the front-driven Elan of 1989.
    "Lotus": (_until(1988, "rwd"),),
    # American makes, until the year before their first front-driven car went into
    # production (a model year starts the autumn before): the Toronado of 1966, the
    # Eldorado of 1967, the Omni and Horizon of 1978, the Riviera and the X-cars of
    # 1979, the Continental of 1988. Chrysler stops for the European Alpine of 1975.
    "Oldsmobile": (_until(1964, "rwd"),),
    "Cadillac": (_until(1965, "rwd"),),
    "Chevrolet": (_until(1978, "rwd"),),
    "Pontiac": (_until(1978, "rwd"),),
    "Buick": (_until(1977, "rwd"),),
    "Chrysler": (_until(1974, "rwd"),),
    "Dodge": (_until(1976, "rwd"),),
    "Plymouth": (_until(1976, "rwd"),),
    "Mercury": (_until(1977, "rwd"),),
    "Lincoln": (_until(1986, "rwd"),),
    "SEAT": (_between(1990, 2019, "fwd"),),
    "MG": (_until(1980, "rwd"),),
    "DKW": (Era("fwd"),),
    "Trabant": (Era("fwd"),),
    "Lloyd": (Era("fwd"),),
    "Rambler": (Era("rwd"),),
    "AMC": (Era("rwd"),),
    "Studebaker": (Era("rwd"),),
    "Hudson": (Era("rwd"),),
    "Packard": (Era("rwd"),),
    "DeSoto": (Era("rwd"),),
    "Edsel": (Era("rwd"),),
    "Imperial": (Era("rwd"),),
    "Rolls-Royce": (Era("rwd"),),
    "Bentley": (Era("rwd"),),
    "Ferrari": (Era("rwd"),),
    "Maserati": (Era("rwd"),),
    "Aston Martin": (Era("rwd"),),
    "Morgan": (Era("rwd"),),
    "TVR": (Era("rwd"),),
    "Jensen": (Era("rwd"),),
    "De Tomaso": (Era("rwd"),),
    "DAF": (Era("rwd"),),
    "Daimler": (Era("rwd"),),
    "Borgward": (Era("rwd"),),
    "Hillman": (Era("rwd"),),
}


def _build() -> dict[tuple[str, str], tuple[Era, ...]]:
    table: dict[tuple[str, str], tuple[Era, ...]] = {}
    for make, layouts in _ONE_LAYOUT.items():
        for layout, models in layouts.items():
            if isinstance(models, str):
                raise TypeError(f"{make} {layout}: one model needs its trailing comma")
            for model in models:
                key = (make, model)
                if key in table or key in _BY_ERA:
                    raise ValueError(f"{make} {model} is stated twice")
                table[key] = (Era(layout),)
    table.update(_BY_ERA)
    return table


#: (make, model family) -> the model's layouts.
REVIEWED_DRIVE_LAYOUTS: dict[tuple[str, str], tuple[Era, ...]] = _build()


def drive_layout(
    manufacturer: str | None, model_family: str | None, year: int | None, fuel: str | None
) -> Layout | None:
    """The driven axle of a two-wheel-drive car of this make, model, year and fuel.

    None when the table does not state it: the model is not reviewed, it was sold
    with either axle driven, or the year falls between two generations.
    """

    if not manufacturer:
        return None
    eras = (
        REVIEWED_DRIVE_LAYOUTS.get((manufacturer, model_family))
        if model_family
        else _MAKE_WITHOUT_MODEL.get(manufacturer)
    )
    for era in eras or ():
        if era.covers(year, fuel):
            return era.layout
    return None


# --- variants: what the model alone does not settle ------------------------------------
#
# The table above answers "front or rear?" for a car the registry marks as not
# four-wheel drive. Two kinds of car are left: one whose model was sold with either
# axle driven, and one the registry makes no four-wheel-drive statement about at all
# (cars known from the inspection register only). For those the variant decides, and
# a variant is told apart by what the car itself carries: its power, its fuel, its
# body, or the registry's own text.

Drive = Literal["fwd", "rwd", "awd"]


@dataclass(frozen=True)
class Variant:
    """One variant of a model. Every condition that is given must hold."""

    drive: Drive
    #: Power in kW, both ends included.
    kw: tuple[int, int] | None = None
    year_from: int | None = None
    year_to: int | None = None
    #: The main fuel.
    fuel: str | None = None
    #: True: petrol or diesel with a plug (a second fuel of electricity).
    plug_in: bool | None = None
    bodies: tuple[str, ...] = ()
    #: A pattern the registry's model text must contain.
    text: str | None = None
    #: True: holds only for a car the registry marks as not four-wheel drive (the
    #: model also came with four driven wheels, which these conditions do not rule out).
    two_wheel: bool = False

    def covers(
        self,
        year: int | None,
        fuel: str | None,
        second_fuel: str | None,
        power_kw: int | None,
        body: str | None,
        text: str | None,
    ) -> bool:
        if self.kw is not None and (power_kw is None or not self.kw[0] <= power_kw <= self.kw[1]):
            return False
        if (self.year_from is not None or self.year_to is not None) and (
            year is None
            or (self.year_from is not None and year < self.year_from)
            or (self.year_to is not None and year > self.year_to)
        ):
            return False
        if self.fuel is not None and fuel != self.fuel:
            return False
        if self.plug_in is not None and (second_fuel == ELECTRIC_FUEL) != self.plug_in:
            return False
        if self.bodies and body not in self.bodies:
            return False
        return self.text is None or bool(text and re.search(self.text, text))


def _electric(drive: Drive, low: int, high: int) -> Variant:
    return Variant(drive, kw=(low, high), fuel=ELECTRIC_FUEL)


_PETROL = "petrol"
_ANY_POWER = 9999

#: (make, model family) -> its variants, first match wins. Electric cars are told
#: apart by power: one motor drives one axle, a second motor adds the other.
_VARIANTS: dict[tuple[str, str], tuple[Variant, ...]] = {
    # The 2026 plug-in hybrid (its 132 kW engine is in no other XC60, and the cars
    # weigh what only the plug-in does): the rear axle is driven electrically.
    # The B6 (220 kW) came with four-wheel drive only.
    ("Volvo", "XC60"): (
        Variant("awd", kw=(130, 134), fuel=_PETROL, plug_in=True, year_from=2025),
        Variant("awd", kw=(218, 222), fuel=_PETROL, year_from=2020),
    ),
    ("Volvo", "V60"): (
        Variant("awd", kw=(130, 134), fuel=_PETROL, plug_in=True, year_from=2025),
    ),
    ("Volvo", "EX30"): (_electric("rwd", 100, 210), _electric("awd", 300, _ANY_POWER)),
    ("Volvo", "EX30 Cross Country"): (
        _electric("rwd", 100, 210), _electric("awd", 300, _ANY_POWER),
    ),
    ("Volvo", "EX90"): (_electric("rwd", 180, 260), _electric("awd", 290, _ANY_POWER)),
    ("Volvo", "EX60"): (_electric("awd", 350, _ANY_POWER),),
    # 2023 was the year the single-motor cars moved from the front axle to the rear.
    ("Volvo", "XC40"): (_electric("fwd", 165, 172), _electric("rwd", 173, 190)),
    ("Volvo", "C40"): (_electric("fwd", 165, 172), _electric("rwd", 173, 190)),
    ("Volvo", "EX40"): (_electric("rwd", 170, 190), _electric("awd", 290, _ANY_POWER)),
    ("Volvo", "EC40"): (_electric("rwd", 170, 190), _electric("awd", 290, _ANY_POWER)),
    ("Polestar", "2"): (
        _electric("fwd", 160, 175), _electric("rwd", 195, 225), _electric("awd", 290, _ANY_POWER),
    ),
    ("Polestar", "3"): (_electric("rwd", 200, 230), _electric("awd", 350, _ANY_POWER)),
    ("Polestar", "4"): (_electric("rwd", 190, 210), _electric("awd", 390, _ANY_POWER)),
    ("Kia", "EV2"): (Variant("fwd"),),
    ("BMW", "iX3"): (_electric("rwd", 200, 220), _electric("awd", 300, _ANY_POWER)),
    ("BMW", "iX1"): (_electric("fwd", 140, 160), _electric("awd", 200, _ANY_POWER)),
    ("BMW", "iX2"): (_electric("fwd", 140, 160), _electric("awd", 200, _ANY_POWER)),
    # The M135 is four-wheel drive only.
    ("BMW", "1 Series"): (Variant("awd", kw=(215, 235), fuel=_PETROL, year_from=2019),),
    # Tourers and the four-door Gran Coupe sit on the front-driven platform; the
    # two-door coupe and convertible drive the rear wheels.
    # (The tourers also came as four-wheel-drive plug-in hybrids.)
    # From 2020 the registry calls the Gran Coupe a coupe too, so a coupe decides
    # only until 2019; an M2 is always the two-door.
    ("BMW", "2 Series"): (
        Variant("rwd", text=r"^M2\b", two_wheel=True),
        Variant("fwd", text=r"TOURER|GRAN COUP", two_wheel=True),
        Variant("fwd", bodies=("multi_purpose_vehicle", "sedan", "estate", "hatchback"),
                two_wheel=True),
        Variant("rwd", bodies=("coupe", "convertible", "open_body"), year_to=2019,
                two_wheel=True),
    ),
    ("Cupra", "Tavascan"): (_electric("rwd", 200, 215), _electric("awd", 240, _ANY_POWER)),
    ("Cupra", "Raval"): (Variant("fwd"),),
    # The plug-in hybrids (a 130 kW petrol engine) drive the front wheels.
    ("Cupra", "Leon"): (Variant("fwd", kw=(128, 132), fuel=_PETROL),),
    ("Cupra", "Formentor"): (Variant("fwd", kw=(128, 132), fuel=_PETROL),),
    # Every combustion GLC since the 2023 model is 4MATIC, and so is the electric one.
    ("Mercedes-Benz", "GLC"): (
        _electric("awd", 300, _ANY_POWER),
        Variant("awd", kw=(260, 280), fuel="diesel", year_from=2023),
    ),
    ("Mercedes-Benz", "GLB"): (_electric("rwd", 190, 210), _electric("awd", 250, _ANY_POWER)),
    ("Mercedes-Benz", "CLA"): (_electric("rwd", 160, 210), _electric("awd", 250, _ANY_POWER)),
    # The eVito and the small diesels drive the front wheels, the large ones the rear.
    ("Mercedes-Benz", "Vito"): (
        Variant("fwd", fuel=ELECTRIC_FUEL, year_from=2014),
        Variant("fwd", kw=(60, 90), fuel="diesel", year_from=2014, two_wheel=True),
        Variant("rwd", kw=(118, 180), fuel="diesel", year_from=2014, two_wheel=True),
    ),
    ("XPENG", "G6"): (_electric("rwd", 180, 260), _electric("awd", 300, _ANY_POWER)),
    ("XPENG", "G9"): (_electric("rwd", 220, 270), _electric("awd", 390, _ANY_POWER)),
    ("Zeekr", "X"): (_electric("rwd", 190, 210), _electric("awd", 300, _ANY_POWER)),
    ("Zeekr", "001"): (_electric("rwd", 190, 210), _electric("awd", 390, _ANY_POWER)),
    ("Zeekr", "7X"): (_electric("rwd", 300, 320), _electric("awd", 450, _ANY_POWER)),
    ("Zeekr", "7GT"): (_electric("rwd", 300, 320), _electric("awd", 450, _ANY_POWER)),
    ("Audi", "Q6"): (_electric("rwd", 180, 250), _electric("awd", 280, _ANY_POWER)),
    ("Tesla", "Model 3"): (_electric("rwd", 150, 260), _electric("awd", 300, _ANY_POWER)),
    ("Tesla", "Model Y"): (_electric("rwd", 150, 260), _electric("awd", 300, _ANY_POWER)),
    # The registry adds the two motors of the four-wheel-drive cars together.
    ("Toyota", "bZ4X"): (
        _electric("fwd", 120, 126), _electric("fwd", 148, 152), _electric("awd", 158, 162),
        _electric("fwd", 164, 168), _electric("awd", 250, _ANY_POWER),
    ),
    ("Toyota", "C-hr"): (_electric("fwd", 120, 170), _electric("awd", 250, _ANY_POWER)),
    ("Subaru", "Solterra"): (_electric("awd", 158, _ANY_POWER),),
    ("Subaru", "Uncharted"): (_electric("fwd", 120, 170), _electric("awd", 250, _ANY_POWER)),
    ("Lexus", "RZ"): (
        _electric("fwd", 148, 152), _electric("fwd", 164, 168), _electric("awd", 225, _ANY_POWER),
    ),
    ("MINI", "Mini Countryman"): (
        _electric("fwd", 140, 160), _electric("awd", 220, _ANY_POWER),
        Variant("fwd", kw=(110, 120), fuel=_PETROL, year_from=2024),
        Variant("awd", kw=(145, 165), fuel=_PETROL, year_from=2024),
    ),
    # The MG4 Urban has less power than any rear-driven MG4.
    ("MG", "MG4"): (
        _electric("fwd", 100, 120), _electric("rwd", 122, 190), _electric("awd", 300, _ANY_POWER),
    ),
    ("Opel", "Grandland"): (Variant("fwd", kw=(90, 110), fuel=_PETROL, year_from=2024),),
    ("Opel", "Frontera"): (Variant("fwd", year_from=2024),),
    # The plug-in Seal U with the turbocharged engine has a second motor at the rear.
    ("BYD", "Seal U"): (
        Variant("awd", kw=(94, 98), fuel=_PETROL), Variant("fwd", kw=(70, 74), fuel=_PETROL),
        _electric("fwd", 150, 170),
    ),
    ("BYD", "Seal"): (_electric("rwd", 150, 240), _electric("awd", 380, _ANY_POWER)),
    ("Jeep", "Avenger"): (
        Variant("fwd", kw=(70, 80), fuel=_PETROL), Variant("awd", kw=(98, 102), fuel=_PETROL),
        Variant("fwd", fuel=ELECTRIC_FUEL),
    ),
    # The rear-driven Corolla estate ran on beside the front-driven saloon until 1987.
    ("Toyota", "Corolla"): (
        Variant("rwd", year_from=1983, year_to=1987, bodies=("estate",), two_wheel=True),
        Variant("fwd", year_from=1984, year_to=1987, bodies=("sedan",), two_wheel=True),
    ),
    ("Ford", "Transit"): (Variant("fwd", text=r"CUSTOM|\bFWD\b", two_wheel=True),),
    # The electric hatchback; the name also covers the four-wheel-drive Countryman.
    ("MINI", "MINI"): (_electric("fwd", 100, 200),),
}

#: Models never sold with four driven wheels: for these the table's layout holds even
#: when the registry makes no four-wheel-drive statement.
_ROAD_CAR_KW = 180
_NEVER_FOUR_WHEEL: frozenset[tuple[str, str]] = frozenset({
    ("Nissan", "Micra"), ("Nissan", "Leaf"),
    ("Toyota", "Aygo X"), ("Toyota", "Aygo"), ("Kia", "K4"), ("Kia", "Picanto"),
    ("Kia", "Rio"), ("Kia", "Ceed"), ("Kia", "Stonic"), ("BYD", "Seal 6"), ("BYD", "Dolphin"),
    ("BYD", "Atto 2"), ("Renault", "Clio"), ("Renault", "5"), ("Dacia", "Sandero"),
    ("Dacia", "Logan"), ("Dacia", "Jogger"), ("Volkswagen", "Polo"), ("Volkswagen", "Up!"),
    ("Volkswagen", "T-cross"), ("Volkswagen", "Taigo"), ("Škoda", "Fabia"), ("Škoda", "Kamiq"),
    ("Škoda", "Scala"), ("SEAT", "Ibiza"), ("SEAT", "Arona"), ("Hyundai", "i10"),
    ("Hyundai", "i20"), ("Hyundai", "i30"), ("Hyundai", "Inster"), ("Hyundai", "Bayon"),
    ("Cupra", "Born"), ("Smart", "Fortwo"), ("Honda", "Jazz"), ("Honda", "e"),
    ("Peugeot", "208"), ("Peugeot", "2008"), ("Peugeot", "308"), ("Citroën", "C3"),
    ("Opel", "Corsa"), ("Ford", "Fiesta"), ("Mazda", "2"), ("Mazda", "MX-5"),
})
#: Of those, the models the table above does not name.
_NEVER_FOUR_WHEEL_ADDED: dict[tuple[str, str], Layout] = {
    ("Kia", "K4"): "fwd", ("BYD", "Seal 6"): "fwd", ("BYD", "Dolphin"): "fwd",
    ("BYD", "Atto 2"): "fwd", ("Hyundai", "i30"): "fwd",
}

#: Registry text of cars that carry no model, by make: (pattern, layout, first year,
#: last year). The text names the model the model families did not read. Asked only
#: for a car the registry marks as not four-wheel drive; first match wins.
_BY_TEXT: dict[str, tuple[tuple[str, Layout, int | None, int | None], ...]] = {
    "Volkswagen": (
        # The Beetle and the Type 3, by their engine sizes; the buses up to the T3.
        (r"\b(1200|1300|1302|1303|1500)\b|KARMANN", "rwd", None, 1985),
        (r"\b1600\b", "rwd", None, 1975),
        (r"KLEINBUS", "rwd", None, 1990),
    ),
    "Ford": (
        ((r"MUSTANG|GALAXIE|ANGLIA|FAIRLANE|CORSAIR|PINTO|CORTINA|ZEPHYR|ZODIAC|CONSUL|CAPRI"
         r"|THUNDERBIRD|GRANADA|SIERRA|SCORPIO|\b(17|20|26) ?M\b"), "rwd", None, None),
        (r"FOCUS|C-MAX|FIESTA|MONDEO|TAURUS|TOURNEO CONN|\b(12|15) ?M\b", "fwd", None, None),
    ),
    "Renault": (
        (r"DAUPHINE|FLORIDE|CARAVELLE|GORDINI|4 ?CV|\bR ?(8|10)\b|ONDINE", "rwd", None, 1976),
        # After the rear-engined cars every Renault car drove the front wheels; the
        # vans and the Alpines, which did not, are named in the text.
        (r"^RENAULT (?!.*(ALPINE|TURBO 2|MASTER|TRAFIC|MASCOTT|MESSENGER|SPIDER|GTA|A ?[36]10))\S",
         "fwd", 1977, 2013),
    ),
    "Peugeot": (
        (r"\b(202|203|302|402|403|404|504|505|604)\b", "rwd", None, None),
        (r"\b(104|204|304|305|205|309|405|605|106|306|406)\b", "fwd", None, None),
    ),
    "BMC": (
        (r"\b(850|1000|COOPER|MINI|ELF|HORNET|MOKE)\b|MG 1[13]00", "fwd", None, None),
        (r"A-? ?40|\b1600\b|MIDGET|MGC|MGB|6/110|TOURER", "rwd", None, None),
    ),
    "Austin": (
        (r"HEA?LE?Y|HAELEY|SPRITE|\bA ?(35|40|55|60|152)\b|FX4|TAXI|METROPOLITAN", "rwd", None, None),
        (r"MINI|\b850\b|MONTEGO|METRO|MAESTRO|ALLEGRO", "fwd", None, None),
    ),
    "Chevrolet": (
        (r"BERETTA|BETETTA|LUMINA|CITATION|EVANDA|CORSICA|CAVALIER", "fwd", None, None),
        ((r"CAPRIC|CORVETT|\bVAN\b|\bG ?(10|15|20|25|30|1500|2500|3500)\b|\bCG ?\d|\bCM ?1"
         r"|ASTRO|CAMARO|\b1BN|\b1 BN|\b1AW"), "rwd", None, None),
    ),
    "Alfa Romeo": (
        (r"ALFASUD|\b33\b|\b14[567]\b|\b15[569]\b|\b16[46]\b|\b939\b", "fwd", None, None),
        ((r"GT JUNIOR|\b(1300|1600|1750|2000) ?(GT|BERLINA|SPIDER|VELOCE)|MONTREAL|ALFA 90"
         r"|\b90 \d|GIULIA|ALFETTA|SPIDERVELOCE|\b75\b"), "rwd", None, None),
    ),
    "Datsun": (
        (r"CHERRY|\b1[02]0 ?A\b|STANZA", "fwd", None, None),
        ((r"\b(240|260|280) ?Z|\b(120|140) ?Y|\b180 ?B|\b160 ?J|\b(220|240|260) ?C\b|BLUE ?BIRD"
         r"|LAUREL|\b1200\b|\b1600\b"), "rwd", None, 1983),
    ),
    "Pontiac": (
        ((r"TRANS ?SPORT|TRANSPORT|TRANSSPORTER|MONTANA|GRAND PRIX|BONN?EVILLE|\bBON\b|SUNBIRD"
         r"|SUNFIRE|GRAND AM|TORRENT|\b2WP|\b2WJ|\b2HZ"), "fwd", 1987, None),
        (r"TRANS[- ]?A[MK]|\bT/A\b|TANS\b|FIREB|FORMULA|FIE?RE?RO|\bGTO\b", "rwd", None, None),
    ),
    "Oldsmobile": (
        (r"TORONADO|TORNADO|SILHOUETTE|AURORA", "fwd", None, None),
        ((r"F[- ]?85|CUTT?LASS?|VISTA CRUISER|JETSTAR|DYNAMIC|4-4-2|\b442\b|HOLIDAY|NINETY"
         r"|NINTEY|DELTA"), "rwd", None, 1981),
        (r"CUSTOM CRUISE", "rwd", None, 1992),
    ),
    "Fiat": (
        ((r"\b12[78]\b|RITMO|\bUNO\b|PANDA|TIPO|DUCATO|\b280\b|\b238\b|DETHLEFFS|KNAUS|B[UÜ]RSTNER"
         r"|\bLMC\b|TABBERT|ADRIATIK|HYMER"), "fwd", None, None),
        ((r"\b500 ?[FLDR]?\b|\b600\b|\b850\b|SPIDER|\b12[45]\b|\b13[012]\b|\b(1100|1400|1500|1800"
         r"|2100|2300)\b|DINO|X ?1/?9|\b8 ?V\b"), "rwd", None, 1985),
    ),
    "Triumph": (
        (r"\b1300\b", "fwd", 1965, 1970),
        ((r"\bTR ?\d|SPITF|SPRITF|HER[AO]LD|\bGT\b|\b2000|2,5 ?PI|STAG|ROADSTER|MAYFLOWER|VITESSE"
         r"|DOLOMITE"), "rwd", None, None),
    ),
    "Mazda": (
        (r"\b929\b|COSMO|\bRX-? ?\d|MX-? ?5|E2000", "rwd", None, None),
        (r"\b3 (KOMBISEDAN|SEDAN)|\b121\b|MX-? ?[36]|\b323|XEDOS|PROTEGE|\b626\b", "fwd", None, None),
        (r"\bMPV\b", "fwd", 1999, None),
    ),
    "Nissan": (
        (r"\b(180|200|240) ?[SZ]X|\b300 ?(ZX|XZ)|LAUREL|SILVIA|VANETTE", "rwd", None, None),
        (r"BLUEBIRD|CHERRY|STANZA|\b100 ?NX|ALTIMA|QUEST", "fwd", 1983, None),
    ),
    "Toyota": (
        (r"^TOYOTA ZN\b|^(GT ?)?86$|FR-S|MODELL? F\b|CRESSIDA|SOARER|SUPRA|MR ?(II|2)\b|CROWN",
         "rwd", None, None),
        (r"CORO?L+A+|PASEO|TERCEL|SCION XB|CAMRY", "fwd", 1988, None),
    ),
    "Volvo": (
        # The 400 series by its type codes (KX, LX, EX and 483-...); the 300 series by its numbers.
        (r"\b[KLE]X ?\d{3}|\b483-|\b4[468]0\b|\bV70|\bS70|\b850\b", "fwd", 1986, None),
        (r"\b3[46]\d\b|\b[279][46][0-5]\b", "rwd", None, 1998),
    ),
    "Mercedes-Benz": (
        (r"\b11[0-4] ?(CDI|D\b|KOMBI)|VITO", "fwd", 1996, 2003),
        ((r"\bE ?(50|55|63|200|220|230|240|280|300|320|430)|\bSLK? ?\d{3}|\bCLK ?\d|\bC ?36\b"
         r"|\b300 ?DT?\b|\b4(12|16) ?(CDI|D)\b"), "rwd", None, None),
    ),
}


def _text_layout(manufacturer: str, year: int | None, text: str | None) -> Layout | None:
    if not text:
        return None
    for pattern, layout, first, last in _BY_TEXT.get(manufacturer, ()):
        if (first is not None or last is not None) and (
            year is None
            or (first is not None and year < first)
            or (last is not None and year > last)
        ):
            continue
        if re.search(pattern, text):
            return layout
    return None


def drive_variant(
    manufacturer: str | None,
    model_family: str | None,
    year: int | None,
    fuel: str | None,
    second_fuel: str | None,
    power_kw: int | None,
    body: str | None,
    text: str | None,
    four_wheel: bool | None,
) -> Drive | None:
    """The drive type of a car the table and the cars alike have left open.

    `four_wheel` is the registry's statement: False for "not four-wheel drive", None
    when it says nothing. A variant that would contradict the statement states
    nothing, and so does everything that is not certain.
    """

    if not manufacturer or four_wheel:
        return None
    if model_family:
        for variant in _VARIANTS.get((manufacturer, model_family), ()):
            if variant.two_wheel and four_wheel is not False:
                continue
            if variant.covers(year, fuel, second_fuel, power_kw, body, text):
                return None if variant.drive == "awd" and four_wheel is False else variant.drive
        # (A rally car built on one of these models is four-wheel drive; its power gives it away.)
        if (
            four_wheel is None
            and (manufacturer, model_family) in _NEVER_FOUR_WHEEL
            and (power_kw is None or power_kw <= _ROAD_CAR_KW)
        ):
            return _NEVER_FOUR_WHEEL_ADDED.get((manufacturer, model_family)) or drive_layout(
                manufacturer, model_family, year, fuel
            )
        return None
    # No model: the registry's text may still name it, for a two-wheel-drive car.
    return _text_layout(manufacturer, year, text) if four_wheel is False else None
