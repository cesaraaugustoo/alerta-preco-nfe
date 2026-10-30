"""A leitura do XML da NF-e e da pasta de notas.

Lê com expressões regulares, sem parser de XML, de propósito: esta leitura é o porte de um leitor em
JavaScript que continua em uso, e as duas têm de dar a mesma conta. As fixtures de `tests/fixtures`
travam isso, com a saída esperada gerada pelo próprio JavaScript. Por isso as funções abaixo imitam o
JavaScript onde o Python faria diferente: `parseFloat`, `trim`, `\\s`, `\\d`, `Math.round` e a soma.
"""

import math
import os
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from .modelos import Item, Nota

# Natureza da operação que não é compra: sem cobrança, o item não tem valor.
TERMOS_NAO_COMERCIAL = ("BONIFICA", "BRINDE", "REMESSA", "DOACAO", "DEMONSTRA", "AMOSTRA", "DEVOL")
# Diferença tolerada entre as duas leituras, por valor.
TOLERANCIA = 0.005

_ESPACO_JS = (
    " \t\n\v\f\r             "
    "    　﻿"
)
_S_JS = "[" + _ESPACO_JS + ">]"
_PARSE_FLOAT = re.compile(r"[+-]?(?:Infinity|[0-9]+\.?[0-9]*(?:[eE][+-]?[0-9]+)?|\.[0-9]+(?:[eE][+-]?[0-9]+)?)")
_CHAVE = re.compile(r"<chNFe>([0-9]{44})</chNFe>")
_CHAVE_NO_ID = re.compile(r'Id="NFe([0-9]{44})"')
_TPAG = re.compile(r"<tPag>([0-9]+)</tPag>")
_COBR = re.compile(r"<cobr" + _S_JS)
_VPAG = re.compile(r"<vPag>([0-9.]+)</vPag>")
_DUP = re.compile(r"<dup[^>]*>([\s\S]*?)</dup>")
_DET = re.compile(r"<det[^>]*>([\s\S]*?)</det>")


def _parse_float(texto: str) -> float:
    """`parseFloat(texto) || 0`: lê o começo numérico do texto; sem número, ou com zero, dá 0."""
    achado = _PARSE_FLOAT.match(texto.lstrip(_ESPACO_JS))
    return (float(achado[0]) if achado else 0.0) or 0.0


def _tag(bloco: str, nome: str) -> str:
    achado = re.search(f"<{nome}[^>]*>([^<]*)</{nome}>", bloco)
    return achado[1].strip(_ESPACO_JS) if achado else ""


def _numero(bloco: str, nome: str) -> float:
    return _parse_float(_tag(bloco, nome))


def _bloco(xml: str, nome: str) -> str:
    achado = re.search(f"<{nome}{_S_JS}[\\s\\S]*?</{nome}>", xml)
    return achado[0] if achado else ""


def _somar(valores: Iterable[float]) -> float:
    """Soma como o JavaScript. O `sum` do Python compensa o erro de ponto flutuante desde a 3.12 e dá outro número."""
    total = 0.0
    for valor in valores:
        total += valor
    return total


def _arredondar_centavos(valor: float) -> float:
    """`Math.round(x * 100) / 100`: o meio centavo sobe, ao contrário do `round` do Python, que vai para o par."""
    return math.floor(valor * 100 + 0.5) / 100


def _comercial(nat_op: str, t_pag: str, tem_cobr: bool, soma_v_pag: float, soma_v_dup: float) -> bool:
    if soma_v_dup > 0:  # duplicata com valor é prova de cobrança; o tPag é só declaração do emissor
        return True
    if t_pag == "90":
        return False
    if not tem_cobr and soma_v_pag == 0:
        return False
    if not nat_op:
        return True
    natureza = nat_op.upper()
    return not any(termo in natureza for termo in TERMOS_NAO_COMERCIAL)


def _numero_da_nota(texto: str) -> str:
    """Tira o zero à esquerda do número da nota. Nunca faça isso com o código do produto."""
    return str(int(texto)) if texto.isascii() and texto.isdigit() and int(texto) else texto


def _valor_do_item(bloco: str) -> float:
    icms10 = "<ICMS10>" in bloco
    return (
        _numero(bloco, "vProd")
        + _numero(bloco, "vOutro")
        + (_numero(bloco, "vIPI") if "<IPITrib>" in bloco else 0.0)
        + (_numero(bloco, "vICMSST") if icms10 else 0.0)
        + (_numero(bloco, "vFCPST") if icms10 else 0.0)
        + _numero(bloco, "vFrete")
    )


def ler_nota_xml(xml: str) -> Nota | None:
    """Cabeçalho e itens de uma NF-e; None se o texto não tiver chave de acesso."""
    achado = _CHAVE.search(xml) or _CHAVE_NO_ID.search(xml)
    if not achado:
        return None
    ide, emit, dest = _bloco(xml, "ide"), _bloco(xml, "emit"), _bloco(xml, "dest")

    nat_op = _tag(xml, "natOp")
    t_pag = _TPAG.search(xml)
    soma_v_pag = _somar(_parse_float(v) for v in _VPAG.findall(xml))
    soma_v_dup = _somar(_numero(d, "vDup") for d in _DUP.findall(xml))
    comercial = _comercial(nat_op, t_pag[1] if t_pag else "", bool(_COBR.search(xml)), soma_v_pag, soma_v_dup)
    boleto = soma_v_dup if soma_v_dup > 0 else _numero(xml, "vNF")

    blocos = [b for b in _DET.findall(xml) if _tag(b, "xProd")]
    valores = [_valor_do_item(b) if comercial else 0.0 for b in blocos]

    # Rateio proporcional: a soma dos itens fecha com o boleto.
    if comercial and boleto > 0 and blocos:
        soma = _somar(valores)
        if soma > 0 and abs(soma - boleto) > 0.01:
            fator = boleto / soma
            valores = [_arredondar_centavos(v * fator) for v in valores]

    itens = tuple(
        Item(
            codigo_produto=_tag(b, "cProd") or None,
            descricao=_tag(b, "xProd"),
            quantidade=_numero(b, "qCom"),
            unidade=_tag(b, "uCom"),
            valor_total=_numero(b, "vProd"),
            valor_nf_item=valor,
            quantidade_tributada=_numero(b, "qTrib") or None,
            unidade_tributada=_tag(b, "uTrib") or None,
        )
        for b, valor in zip(blocos, valores, strict=True)
    )
    return Nota(
        chave_acesso=achado[1],
        numero=_numero_da_nota(_tag(ide, "nNF")),
        data_emissao=(_tag(ide, "dhEmi") or _tag(ide, "dEmi"))[:10],
        natureza_operacao=nat_op or None,
        valor_fatura=boleto if comercial else 0.0,
        emitente_cnpj=_tag(emit, "CNPJ") or None,
        emitente_nome=_tag(emit, "xNome") or None,
        destinatario_cnpj=_tag(dest, "CNPJ") or None,
        itens=itens,
    )


def ler_arquivo(caminho: Path) -> Nota | None:
    """Lê os bytes crus: abrir em modo texto trocaria a quebra de linha, e o JavaScript não troca."""
    return ler_nota_xml(caminho.read_bytes().decode("utf-8", errors="replace"))


def como_dict(nota: Nota) -> dict[str, Any]:
    """A nota no formato do JavaScript, para comparar as duas leituras."""
    return {
        "chave_acesso": nota.chave_acesso,
        "numero": nota.numero,
        "data_emissao": nota.data_emissao,
        "natureza_operacao": nota.natureza_operacao,
        "valor_fatura": nota.valor_fatura,
        "emitente": {"cnpj": nota.emitente_cnpj, "nome": nota.emitente_nome},
        "destinatario_cnpj": nota.destinatario_cnpj,
        "itens": [asdict(item) for item in nota.itens],
    }


def arquivos_xml(raiz: Path, anos: Iterable[int] | None = None) -> list[Path]:
    """Os `*.xml` em qualquer nível, sem a pasta TESTE, em ordem. Com `anos`, só dentro de `<raiz>/<ano>`."""
    inicios = [raiz] if anos is None else [raiz / str(ano) for ano in anos]
    achados: list[Path] = []
    for inicio in inicios:
        for pasta, subpastas, arquivos in os.walk(inicio):
            subpastas[:] = [s for s in subpastas if s.upper() != "TESTE"]
            achados += [Path(pasta, a) for a in arquivos if a.lower().endswith(".xml")]
    return sorted(achados)


@dataclass(frozen=True, slots=True)
class LeituraDaPasta:
    notas: list[Nota]
    ilegiveis: int


def ler_pasta(raiz: Path, de: str, ate: str) -> LeituraDaPasta:
    """As notas emitidas entre `de` e `ate` (AAAA-MM-DD); a mesma chave conta uma vez."""
    if not raiz.is_dir():
        raise FileNotFoundError(f"Pasta das notas não encontrada: {raiz}")
    por_chave: dict[str, Nota] = {}
    ilegiveis = 0
    for arquivo in arquivos_xml(raiz, range(int(de[:4]), int(ate[:4]) + 1)):
        try:
            nota = ler_arquivo(arquivo)
        except OSError:
            nota = None
        if nota is None:
            ilegiveis += 1
        elif de <= nota.data_emissao <= ate:
            por_chave.setdefault(nota.chave_acesso, nota)
    return LeituraDaPasta(list(por_chave.values()), ilegiveis)


def _diferencas(esperado: object, obtido: object, caminho: str) -> Iterator[str]:
    if isinstance(esperado, dict) and isinstance(obtido, dict):
        for chave in sorted(esperado.keys() | obtido.keys()):
            yield from _diferencas(esperado.get(chave), obtido.get(chave), f"{caminho}.{chave}")
    elif isinstance(esperado, list) and isinstance(obtido, list):
        if len(esperado) != len(obtido):
            yield f"{caminho}: JavaScript com {len(esperado)}, Python com {len(obtido)}"
        for i, (e, o) in enumerate(zip(esperado, obtido, strict=False)):
            yield from _diferencas(e, o, f"{caminho}[{i}]")
    elif isinstance(esperado, int | float) and isinstance(obtido, int | float):
        if abs(esperado - obtido) > TOLERANCIA:
            yield f"{caminho}: JavaScript {esperado!r}, Python {obtido!r}"
    elif esperado != obtido:
        yield f"{caminho}: JavaScript {esperado!r}, Python {obtido!r}"


@dataclass(slots=True)
class Conferencia:
    """O resultado de comparar a leitura em Python com a do JavaScript, arquivo a arquivo."""

    arquivos: int = 0
    iguais: int = 0
    notas: int = 0
    itens: int = 0
    total_javascript: float = 0.0
    total_python: float = 0.0
    diferencas: list[str] = field(default_factory=list)
    so_no_gabarito: list[str] = field(default_factory=list)
    so_na_pasta: list[str] = field(default_factory=list)

    @property
    def tudo_igual(self) -> bool:
        return not (self.diferencas or self.so_no_gabarito or self.so_na_pasta)


def _soma_dos_itens(nota: Mapping[str, Any] | None) -> float:
    return _somar(i["valor_nf_item"] for i in nota["itens"]) if nota else 0.0


def conferir_leitura(raiz: Path, gabarito: Mapping[str, Any]) -> Conferencia:
    """Compara cada arquivo da pasta com o `gabarito` do JavaScript: { caminho relativo: nota ou None }."""
    na_pasta = {arquivo.relative_to(raiz).as_posix(): arquivo for arquivo in arquivos_xml(raiz)}
    resultado = Conferencia(
        so_no_gabarito=sorted(gabarito.keys() - na_pasta.keys()),
        so_na_pasta=sorted(na_pasta.keys() - gabarito.keys()),
    )
    for nome in sorted(na_pasta.keys() & gabarito.keys()):
        try:
            nota = ler_arquivo(na_pasta[nome])
        except OSError:
            nota = None
        python = como_dict(nota) if nota else None
        achadas = list(_diferencas(gabarito[nome], python, nome))
        resultado.arquivos += 1
        resultado.iguais += not achadas
        resultado.diferencas += achadas
        resultado.notas += python is not None
        resultado.itens += len(python["itens"]) if python else 0
        resultado.total_javascript += _soma_dos_itens(gabarito[nome])
        resultado.total_python += _soma_dos_itens(python)
    return resultado
