"""lab_helpers.py - funções reutilizáveis do LAB "Felicidade por Idade e por País".

Fontes:
- Our World in Data (OWID), "Self-reported life satisfaction by age"
  (Gallup World Poll via World Happiness Report 2024, média 2021-2023). Licença CC BY.
- Banco Mundial, World Development Indicators (API pública v2).
"""
from __future__ import annotations

import os
import numpy as np
import pandas as pd
import requests

# --------------------------------------------------------------------------
# Constantes
# --------------------------------------------------------------------------
OWID_AGE_URL = ("https://ourworldindata.org/grapher/cantril-ladder-age-groups.csv"
                "?v=1&csvType=full&useColumnShortNames=false")
OWID_LOCAL = "cantril-ladder-age-groups.csv"

# Nome da coluna no CSV do OWID -> rótulo curto da faixa etária
AGE_COLS = {
    "Up to 29 years": "<30",
    "30-44 years": "30-44",
    "45-59 years": "45-59",
    "60+ years": "60+",
}
# Nomes alternativos quando o CSV vem com useColumnShortNames=true
AGE_COLS_SHORT = {
    "cantril_ladder_score__age_group_up_to_29_years": "<30",
    "cantril_ladder_score__age_group_30_44_years": "30-44",
    "cantril_ladder_score__age_group_45_59_years": "45-59",
    "cantril_ladder_score__age_group_60plus_years": "60+",
}
AGE_ORDER = ["<30", "30-44", "45-59", "60+"]

# Ponto médio de cada faixa. "<30" começa em 15 (Gallup entrevista pessoas com 15+).
# "60+" é aberta: 70 é uma SUPOSIÇÃO (teste 65 como robustez).
AGE_MID = {"<30": 22, "30-44": 37, "45-59": 52, "60+": 70}

# Códigos próprios do OWID -> código usado pelo Banco Mundial
OWID_TO_WB = {"OWID_KOS": "XKX"}

WB_INDICATORS = {
    "gdp_pc": "NY.GDP.PCAP.PP.KD",   # PIB per capita, PPC (US$ internacionais constantes)
    "pop": "SP.POP.TOTL",            # população total
    "area_km2": "AG.LND.TOTL.K2",    # área terrestre (km²)
}
WB_YEARS = (2019, 2023)              # janela para o "último valor não faltante"
WB_BASE = "https://api.worldbank.org/v2"


# --------------------------------------------------------------------------
# Parte 1: dados por idade (OWID)
# --------------------------------------------------------------------------
def read_owid_raw(url: str = OWID_AGE_URL, local: str = OWID_LOCAL) -> pd.DataFrame:
    """Lê o CSV largo do OWID. Tenta a URL; se falhar, usa o arquivo local."""
    try:
        raw = pd.read_csv(url, storage_options={"User-Agent": "Mozilla/5.0 (PUCSP lab)"})
    except Exception as err:  # offline, bloqueio etc.
        if not os.path.exists(local):
            raise RuntimeError(f"Falha ao baixar {url} e não achei {local}: {err}")
        print(f"[aviso] download falhou ({type(err).__name__}); usando {local}")
        raw = pd.read_csv(local)
    # padroniza nomes curtos (useColumnShortNames=true) para os nomes longos
    short_to_long = dict(zip(AGE_COLS_SHORT, AGE_COLS))
    return raw.rename(columns=short_to_long)


def load_owid_age(url: str = OWID_AGE_URL, local: str = OWID_LOCAL) -> pd.DataFrame:
    """Formato longo: country, iso3, year, age_group, ladder_mean, window.

    Descarta linhas sem código de país (agregados regionais do OWID não têm Code).
    """
    raw = read_owid_raw(url, local)
    raw = raw.rename(columns=AGE_COLS)
    raw = raw[raw["Code"].notna()].copy()
    age = raw.melt(id_vars=["Entity", "Code", "Year"], value_vars=AGE_ORDER,
                   var_name="age_group", value_name="ladder_mean")
    age = age.rename(columns={"Entity": "country", "Code": "iso3", "Year": "year"})
    age["iso3"] = age["iso3"].replace(OWID_TO_WB)
    age = age.dropna(subset=["ladder_mean"])
    age["age_group"] = pd.Categorical(age["age_group"], categories=AGE_ORDER, ordered=True)
    age["window"] = "2021-2023"
    return age.sort_values(["iso3", "age_group"]).reset_index(drop=True)


# --------------------------------------------------------------------------
# Parte 2: Banco Mundial
# --------------------------------------------------------------------------
def _wb_get(path: str, **params) -> list:
    """Chama a API v2 e junta todas as páginas.

    A resposta é uma lista de 2 elementos: [metadados, registros].
    """
    params = {"format": "json", "per_page": 20000, **params}
    out, page = [], 1
    while True:
        r = requests.get(f"{WB_BASE}/{path}", params={**params, "page": page}, timeout=60)
        r.raise_for_status()
        meta, records = r.json()
        out.extend(records or [])
        if page >= int(meta.get("pages", 1)):
            return out
        page += 1


def wb_country_codes() -> set:
    """ISO3 dos países reais (exclui agregados como 'World', 'Arab World')."""
    rows = _wb_get("country", per_page=500)
    return {c["id"] for c in rows if c["region"]["value"] != "Aggregates"}


def fetch_wb(indicator: str, years: tuple = WB_YEARS) -> pd.Series:
    """Último valor não faltante de `indicator` na janela `years`, indexado por iso3."""
    rows = _wb_get(f"country/all/indicator/{indicator}", date=f"{years[0]}:{years[1]}")
    df = pd.DataFrame({
        "iso3": [r["countryiso3code"] for r in rows],
        "year": [int(r["date"]) for r in rows],
        "value": [r["value"] for r in rows],
    }).dropna(subset=["value"])
    df = df[df["iso3"].str.len() == 3]
    last = df.sort_values("year").groupby("iso3").tail(1).set_index("iso3")
    return last["value"].rename(indicator)


def build_country_table(cache: str | None = "country_table.csv",
                        refresh: bool = False) -> pd.DataFrame:
    """Tabela por país: iso3, gdp_pc, pop, area_km2 (+ ano de cada valor não é guardado).

    Salva um cache em CSV para não depender da API a cada execução.
    """
    if cache and os.path.exists(cache) and not refresh:
        return pd.read_csv(cache)
    countries = wb_country_codes()
    cols = {name: fetch_wb(code) for name, code in WB_INDICATORS.items()}
    table = pd.DataFrame(cols)
    table = table[table.index.isin(countries)]
    table.index.name = "iso3"
    table = table.reset_index()
    if cache:
        table.to_csv(cache, index=False)
    return table


# --------------------------------------------------------------------------
# Chaves de país a partir de nomes
# --------------------------------------------------------------------------
def to_iso3(names: pd.Series, verbose: bool = True) -> pd.Series:
    """Converte nomes de países para ISO3 com country_converter.

    Imprime os nomes sem correspondência e devolve NaN para eles.
    """
    import country_converter as coco
    import logging
    logging.getLogger("country_converter").setLevel(logging.ERROR)
    codes = coco.convert(names.tolist(), to="ISO3", not_found=None)
    if isinstance(codes, str):
        codes = [codes]
    s = pd.Series(codes, index=names.index, dtype="object")
    s = s.where(s.str.len() == 3)  # not_found vira NaN
    missing = names[s.isna()].tolist()
    if verbose and missing:
        print("sem correspondência ISO3:", missing)
    return s


# --------------------------------------------------------------------------
# Plano B: dados sintéticos (NUNCA reporte como resultado real)
# --------------------------------------------------------------------------
def make_synthetic_age_data(country_table: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
    """Simula ladder_mean por faixa etária usando o PIB real + curva em U plantada + ruído."""
    rng = np.random.default_rng(seed)
    ct = country_table.dropna(subset=["gdp_pc"])
    rows = []
    for _, c in ct.iterrows():
        base = 1.0 + 0.55 * np.log(c["gdp_pc"]) + rng.normal(0, 0.5)
        for g in AGE_ORDER:
            a = AGE_MID[g]
            u = 0.0006 * (a - 48) ** 2 - 0.15
            rows.append({"country": c["iso3"], "iso3": c["iso3"], "year": 2023,
                         "age_group": g, "ladder_mean": base + u + rng.normal(0, 0.2),
                         "window": "synthetic", "synthetic": True})
    return pd.DataFrame(rows)
