"""O que já foi avaliado e avisado, e quais notas entram em cada rodada."""

import contextlib
import json
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from .config import DIAS_DE_ATRASO

CAMINHO_PADRAO = Path.home() / "alerta-preco" / "estado.json"


class ErroDeEstado(Exception):
    """O arquivo de estado está ilegível; a rodada para, em vez de repetir ou perder avisos."""


@dataclass(slots=True)
class Estado:
    semanas_enviadas: list[str] = field(default_factory=list)
    notas_avaliadas: set[str] = field(default_factory=set)
    ultimo_avisado: dict[str, float] = field(default_factory=dict)
    # O último aviso de antes da semana mais recente, para --reenviar não suprimir os próprios alertas dela.
    semana_do_envio: str | None = None
    avisado_antes: dict[str, float] = field(default_factory=dict)


def _segunda(dia: date) -> date:
    return dia - timedelta(days=dia.weekday())


def semana_anterior(hoje: date) -> date:
    """A segunda-feira da semana passada."""
    return _segunda(hoje) - timedelta(days=7)


def semanas_entre(de: date, ate: date) -> list[tuple[str, str]]:
    """As semanas, de segunda a domingo, que cobrem o intervalo; a última corta na data final."""
    semanas: list[tuple[str, str]] = []
    inicio = _segunda(de)
    while inicio <= ate:
        fim = min(inicio + timedelta(days=6), ate)
        semanas.append((inicio.isoformat(), fim.isoformat()))
        inicio += timedelta(days=7)
    return semanas


@dataclass(frozen=True, slots=True)
class NotasDaRodada:
    chaves: set[str]
    atrasadas: set[str]


def notas_da_rodada(notas: Iterable[tuple[str, str]], segunda: date, estado: Estado | None) -> NotasDaRodada:
    """A semana, mais as notas (chave, data de emissão) dos dias anteriores que ainda não foram avaliadas.

    O atraso nunca volta para antes da primeira rodada enviada: o que veio antes dela não foi perdido, só
    não existia rotina, e viraria uma lista falsa de "chegou depois" na segunda rodada.
    """
    de, ate = segunda.isoformat(), (segunda + timedelta(days=6)).isoformat()
    lista = list(notas)
    chaves = {chave for chave, data in lista if de <= data <= ate}
    atrasadas: set[str] = set()
    if estado is not None and estado.semanas_enviadas:
        janela = (segunda - timedelta(days=DIAS_DE_ATRASO)).isoformat()
        inicio = max(janela, min(estado.semanas_enviadas))
        atrasadas = {c for c, data in lista if inicio <= data < de and c not in estado.notas_avaliadas}
    return NotasDaRodada(chaves | atrasadas, atrasadas)


def ja_enviada(estado: Estado | None, segunda: date) -> bool:
    return estado is not None and segunda.isoformat() in estado.semanas_enviadas


def avisados_da_rodada(estado: Estado | None, segunda: date, reenviar: bool) -> dict[str, float]:
    """Contra o que comparar a repetição: no reenvio da mesma semana, o que valia antes dela."""
    if estado is None:
        return {}
    if reenviar and estado.semana_do_envio == segunda.isoformat():
        return dict(estado.avisado_antes)
    return dict(estado.ultimo_avisado)


def registrar_envio(
    estado: Estado | None, segunda: date, chaves: Iterable[str], avisados: Mapping[str, float]
) -> Estado:
    """O estado depois de o e-mail sair. Não mexe no recebido; quem grava é `gravar_estado`."""
    atual = estado or Estado()
    semana = segunda.isoformat()
    antes = atual.avisado_antes if atual.semana_do_envio == semana else atual.ultimo_avisado
    semanas = atual.semanas_enviadas if semana in atual.semanas_enviadas else [*atual.semanas_enviadas, semana]
    return Estado(
        semanas_enviadas=list(semanas),
        notas_avaliadas=atual.notas_avaliadas | set(chaves),
        ultimo_avisado={**antes, **avisados},
        semana_do_envio=semana,
        avisado_antes=dict(antes),
    )


def _textos(dados: dict[str, Any], campo: str) -> list[str]:
    valor = dados.get(campo, [])
    if not isinstance(valor, list) or not all(isinstance(v, str) for v in valor):
        raise ValueError(campo)
    return valor


def _precos(dados: dict[str, Any], campo: str) -> dict[str, float]:
    valor = dados.get(campo, {})
    if not isinstance(valor, dict) or not all(isinstance(v, int | float) for v in valor.values()):
        raise ValueError(campo)
    return valor


def carregar_estado(caminho: Path | None = None) -> Estado | None:
    """O estado gravado; None na primeira rodada."""
    arquivo = caminho or CAMINHO_PADRAO
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        if not isinstance(dados, dict):
            raise ValueError("raiz")
        antes = dados.get("avisado_antes") or {}
        if not isinstance(antes, dict):
            raise ValueError("avisado_antes")
        return Estado(
            semanas_enviadas=_textos(dados, "semanas_enviadas"),
            notas_avaliadas=set(_textos(dados, "notas_avaliadas")),
            ultimo_avisado=_precos(dados, "ultimo_avisado"),
            semana_do_envio=antes.get("semana"),
            avisado_antes=_precos(antes, "ultimo_avisado"),
        )
    except FileNotFoundError:
        return None
    except ValueError as e:
        raise ErroDeEstado(f"Estado ilegível em {arquivo}: {e}. Confira o arquivo antes de rodar de novo.") from e


def gravar_estado(estado: Estado, caminho: Path | None = None) -> None:
    """Grava num arquivo temporário e troca: uma queda no meio não deixa estado pela metade."""
    arquivo = caminho or CAMINHO_PADRAO
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    dados: dict[str, Any] = {
        "semanas_enviadas": estado.semanas_enviadas,
        "notas_avaliadas": sorted(estado.notas_avaliadas),
        "ultimo_avisado": estado.ultimo_avisado,
    }
    if estado.semana_do_envio:
        dados["avisado_antes"] = {"semana": estado.semana_do_envio, "ultimo_avisado": estado.avisado_antes}
    descritor, temporario = tempfile.mkstemp(dir=arquivo.parent, prefix=".estado-", suffix=".tmp")
    try:
        with os.fdopen(descritor, "w", encoding="utf-8") as f:
            json.dump(dados, f, ensure_ascii=False, indent=2)
        os.replace(temporario, arquivo)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporario)
        raise
