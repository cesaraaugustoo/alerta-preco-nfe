"""A linha de comando."""

import argparse
import io
import json
import sys
from pathlib import Path

from . import config
from .xml_nota import conferir_leitura

DIFERENCAS_NA_TELA = 20


def _brl(valor: float) -> str:
    return f"R$ {valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def _conferir_leitura(arquivo: Path) -> int:
    pasta = config.carregar_config().pasta_notas
    gabarito = json.loads(arquivo.read_text(encoding="utf-8"))
    c = conferir_leitura(pasta, gabarito)
    print(f"Arquivos comparados: {c.arquivos}; iguais: {c.iguais} de {c.arquivos}.")
    print(f"Notas lidas: {c.notas}; itens: {c.itens}.")
    print(f"Soma dos itens: JavaScript {_brl(c.total_javascript)}; Python {_brl(c.total_python)}.")
    for titulo, nomes in (("Só no gabarito", c.so_no_gabarito), ("Só na pasta", c.so_na_pasta)):
        if nomes:
            print(f"{titulo}: {len(nomes)}.", *nomes[:DIFERENCAS_NA_TELA], sep="\n  ")
    if c.diferencas:
        print(f"Diferenças: {len(c.diferencas)}.", *c.diferencas[:DIFERENCAS_NA_TELA], sep="\n  ")
    print("Tudo igual." if c.tudo_igual else "HÁ DIFERENÇAS.")
    return 0 if c.tudo_igual else 1


def main(argumentos: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):  # no Windows, o padrão não é UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="alerta_preco", description="Relatório semanal de altas de preço.")
    parser.add_argument(
        "--conferir-leitura", type=Path, metavar="ARQUIVO", help="compara a leitura com a do JavaScript"
    )
    args = parser.parse_args(argumentos)
    try:
        if args.conferir_leitura:
            return _conferir_leitura(args.conferir_leitura)
    except (config.ErroDeConfiguracao, OSError, ValueError) as e:
        print(e, file=sys.stderr)
        return 1
    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
