"""Os limites da regra e a leitura da configuração local."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Alta mínima para avisar: abaixo disso, a variação comum de preço viraria ruído.
LIMITE_ALTA = 0.05
# Custo mínimo da alta na semana, em reais: alta grande em item de pouco valor não pede ação.
MINIMO_REAIS = 20
# Compras anteriores na base: a mediana de poucas compras não deixa um preço errado virar o novo normal.
COMPRAS_NA_BASE = 4
# Dentro desta folga do último preço avisado, a alta já é conhecida e não se repete.
FOLGA_REPETICAO = 0.02
# Boleto fora desta faixa, em relação aos itens: o rateio criaria um preço falso, e a nota fica fora do cálculo.
BOLETO_MIN = 0.97
BOLETO_MAX = 1.35
# Quanto do passado é lido para montar a base.
DIAS_DE_HISTORICO = 120
# Até quantos dias antes da semana uma nota atrasada ainda é avaliada.
DIAS_DE_ATRASO = 14

CAMINHO_PADRAO = Path.home() / "alerta-preco" / "config.toml"


class ErroDeConfiguracao(Exception):
    """A configuração local falta ou está errada; a mensagem diz o que corrigir."""


@dataclass(frozen=True, slots=True)
class Config:
    pasta_notas: Path
    pasta_negocio: Path
    arquivo_env: Path
    vps_ssh: str
    arquivo_env_remoto: str
    destinatarios_extras: tuple[str, ...]


def _texto(dados: dict[str, Any], secao: str, campo: str) -> str:
    valor = dados.get(secao, {}).get(campo)
    if not isinstance(valor, str) or not valor:
        raise ErroDeConfiguracao(f"Falta {secao}.{campo} na configuração.")
    return valor


def _extras(dados: dict[str, Any]) -> tuple[str, ...]:
    valor = dados.get("envio", {}).get("destinatarios_extras", [])
    if not isinstance(valor, list) or not all(isinstance(e, str) for e in valor):
        raise ErroDeConfiguracao("envio.destinatarios_extras tem de ser uma lista de e-mails.")
    return tuple(valor)


def ler_config(caminho: Path | None = None) -> Config:
    """Lê os campos da configuração, sem conferir se os caminhos existem."""
    arquivo = caminho or CAMINHO_PADRAO
    try:
        with arquivo.open("rb") as f:
            dados = tomllib.load(f)
    except FileNotFoundError as e:
        raise ErroDeConfiguracao(f"Configuração não encontrada: {arquivo}. Copie config.example.toml para lá.") from e
    except tomllib.TOMLDecodeError as e:
        raise ErroDeConfiguracao(f"Configuração inválida em {arquivo}: {e}") from e
    return Config(
        pasta_notas=Path(_texto(dados, "fontes", "pasta_notas")),
        pasta_negocio=Path(_texto(dados, "fontes", "pasta_negocio")),
        arquivo_env=Path(_texto(dados, "fontes", "arquivo_env")),
        vps_ssh=_texto(dados, "envio", "vps_ssh"),
        arquivo_env_remoto=_texto(dados, "envio", "arquivo_env_remoto"),
        destinatarios_extras=_extras(dados),
    )


def carregar_config(caminho: Path | None = None) -> Config:
    """Lê a configuração e para, dizendo qual falta, se um caminho não existir."""
    config = ler_config(caminho)
    for nome, pasta in (("pasta_notas", config.pasta_notas), ("pasta_negocio", config.pasta_negocio)):
        if not pasta.is_dir():
            raise ErroDeConfiguracao(f"fontes.{nome} não é uma pasta que exista: {pasta}")
    if not config.arquivo_env.is_file():
        raise ErroDeConfiguracao(f"fontes.arquivo_env não é um arquivo que exista: {config.arquivo_env}")
    return config
