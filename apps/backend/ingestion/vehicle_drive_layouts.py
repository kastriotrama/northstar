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
        "fwd": ("A-Class", "B-Class", "GLA", "GLB", "EQA", "EQB", "T-Class", "Citan"),
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
        "rwd": ("MG4", "Marvel R", "Mgb", "Mga", "Tf", "Mgf", "Midget", "Mgb Gt", "Td"),
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
    ("Volvo", "440"): (_since(1987, "fwd"),),
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
    "Peugeot": (_until(1964, "rwd"), Era("fwd", 1990, None, electric=False)),
    "Citroën": (Era("fwd", 1990, None, electric=False),),
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
    "Rover": (_until(1983, "rwd"),),
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
