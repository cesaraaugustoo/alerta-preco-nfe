"""A linha de comando e a orquestração da rodada."""

import argparse
import contextlib
import io
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from . import config, regra
from .envio import ErroDeEnvio, aviso_na_tela, enviar_email
from .estado import (
    ErroDeEstado,
    avisados_da_rodada,
    carregar_estado,
    gravar_estado,
    ja_enviada,
    notas_da_rodada,
    registrar_envio,
    segunda_da_semana,
    semana_anterior,
    semanas_entre,
    trava_do_envio,
)
from .fontes import Banco, ErroDeFonte, canceladas_no_espiao, ler_credenciais, ler_tabelas
from .modelos import Relatorio
from .relatorio import DadosDoRelatorio, brl, dia_e_mes, intervalo, montar_relatorio, numero
from .xml_nota import ErroDeLeitura, LeituraDaPasta, conferir_leitura, ler_pasta

DIFERENCAS_NA_TELA = 20
_TITULO_DA_FALHA = "Alerta de preço: falhou"
_TITULO_DO_ALERTA = "Alerta de preço: atenção"
ARQUIVO_DE_FALHAS = "falhas.log"  # na pasta local, uma linha por falha, com data, comando e motivo


def _hoje() -> date:
    return date.today()


def _conferir_leitura(arquivo: Path) -> int:
    pasta = config.carregar_config().pasta_notas
    gabarito = json.loads(arquivo.read_text(encoding="utf-8"))
    c = conferir_leitura(pasta, gabarito)
    print(f"Arquivos comparados: {c.arquivos}; iguais: {c.iguais} de {c.arquivos}.")
    print(f"Notas lidas: {c.notas}; itens: {c.itens}.")
    print(f"Soma dos itens: JavaScript {brl(c.total_javascript)}; Python {brl(c.total_python)}.")
    for titulo, nomes in (("Só no gabarito", c.so_no_gabarito), ("Só na pasta", c.so_na_pasta)):
        if nomes:
            print(f"{titulo}: {len(nomes)}.", *nomes[:DIFERENCAS_NA_TELA], sep="\n  ")
    if c.diferencas:
        print(f"Diferenças: {len(c.diferencas)}.", *c.diferencas[:DIFERENCAS_NA_TELA], sep="\n  ")
    print("Tudo igual." if c.tudo_igual else "HÁ DIFERENÇAS.")
    return 0 if c.tudo_igual else 1


def _simular(de_texto: str, ate_texto: str) -> int:
    """Repete as rodadas semana a semana, sem o serviço de notas, sem estado e sem e-mail."""
    de, ate = date.fromisoformat(de_texto), date.fromisoformat(ate_texto)
    cfg = config.carregar_config()
    banco = Banco(ler_credenciais(cfg.arquivo_env))
    tabelas = ler_tabelas(cfg.pasta_negocio)
    leitura = ler_pasta(cfg.pasta_notas, (de - timedelta(days=config.DIAS_DE_HISTORICO)).isoformat(), ate.isoformat())
    filtro = regra.filtrar_notas(leitura.notas, banco.canceladas(), tabelas.notas_fora, tabelas.apelidos)
    semanas = regra.simular(regra.linhas_de_preco(filtro.validas, banco.regras()), semanas_entre(de, ate))

    print(
        f"Notas únicas: {len(leitura.notas)}; ilegíveis: {leitura.ilegiveis}; incompletas: {len(leitura.incompletos)}; "
        f"que não abriram: {len(leitura.falhas)}; anos sem pasta: {len(leitura.anos_ausentes)}; "
        f"na conta: {len(filtro.validas)}; sem item válido: {filtro.sem_item_valido}; canceladas: {filtro.canceladas}; "
        f"na tabela de fora: {filtro.fora_da_tabela}; boleto diferente: {len(filtro.fora_por_boleto)}."
    )
    for semana in semanas:
        print(f"\nDe {dia_e_mes(semana.de)} a {dia_e_mes(semana.ate)}: {len(semana.alertas)} alerta(s).")
        for a in semana.alertas:
            print(
                f"  {a.codigo_produto} | {a.descricao} | base {numero(a.base)} | preço {numero(a.preco)} | "
                f"{numero(a.alta * 100, 1)}% | {brl(a.a_mais)} | {a.como}"
            )
    print(f"\nPor semana: {', '.join(str(len(s.alertas)) for s in semanas)}")
    print(f"Total: {sum(len(s.alertas) for s in semanas)} alerta(s).")
    return 0


@dataclass(frozen=True, slots=True)
class _Rodada:
    relatorio: Relatorio
    chaves: set[str]
    avisados: dict[str, float]


def _exigir_a_pasta_inteira(leitura: LeituraDaPasta, segunda: date) -> None:
    """Sem ter lido tudo, o relatório poderia dizer "nenhuma alta" sem ser verdade: a rodada para antes dele."""
    # No começo de janeiro, a pasta do ano novo ainda pode não existir: só o que vem depois da segunda é tolerado.
    ausentes = [ano for ano in leitura.anos_ausentes if ano <= segunda.year]
    if ausentes:
        raise ErroDeLeitura(f"Falta a pasta do ano {ausentes[0]} nas notas. Nada foi enviado.")
    if leitura.falhas:
        quantos = f"{len(leitura.falhas)} arquivos não abriram" if len(leitura.falhas) > 1 else "1 arquivo não abriu"
        raise ErroDeLeitura(
            f"Na pasta de notas, {quantos} ({leitura.falhas[0]}): ela pode estar sincronizando. Nada foi enviado."
        )


def _montar_rodada(
    cfg: config.Config, segunda: date, hoje: date, teste: bool = False, reenviar: bool = False
) -> _Rodada:
    """Lê as fontes e calcula o relatório da semana. Não envia e não grava estado."""
    estado = carregar_estado()
    banco = Banco(ler_credenciais(cfg.arquivo_env))
    tabelas = ler_tabelas(cfg.pasta_negocio)
    fim = segunda + timedelta(days=6)

    # Lê até hoje, e não até o fim da semana, para o rodapé dizer até onde a pasta chegou.
    leitura = ler_pasta(
        cfg.pasta_notas,
        (segunda - timedelta(days=config.DIAS_DE_HISTORICO)).isoformat(),
        max(hoje, fim).isoformat(),
    )
    _exigir_a_pasta_inteira(leitura, segunda)
    datas = {n.chave_acesso: n.data_emissao for n in leitura.notas}
    rodada = notas_da_rodada(datas.items(), segunda, estado)
    if not rodada.chaves:
        periodo = intervalo(segunda.isoformat(), fim.isoformat())
        raise ErroDeLeitura(f"Nenhuma nota {periodo} na pasta: ela pode estar atrasada. Nada foi enviado.")
    inicio = min([segunda.isoformat(), *(datas[c] for c in rodada.atrasadas)])
    canceladas, falhas = canceladas_no_espiao(banco.configs_espiao(), inicio, fim.isoformat())

    filtro = regra.filtrar_notas(leitura.notas, banco.canceladas() | canceladas, tabelas.notas_fora, tabelas.apelidos)
    linhas = regra.linhas_de_preco(filtro.validas, banco.regras())
    # No ensaio de uma semana já enviada, os alertas dela não podem ser suprimidos pelo próprio aviso.
    avisados = avisados_da_rodada(estado, segunda, reenviar or ja_enviada(estado, segunda))
    avaliacao = regra.avaliar_rodada(linhas, rodada.chaves, avisados, rodada.atrasadas)

    relatorio = montar_relatorio(
        DadosDoRelatorio(
            de=segunda.isoformat(),
            ate=fim.isoformat(),
            alertas=avaliacao.alertas,
            fora_por_boleto=[n for n in filtro.fora_por_boleto if n.chave_acesso in rodada.chaves],
            falhas_de_cancelamento=len(falhas),
            notas_na_rodada=len(rodada.chaves),
            produtos_sem_base=avaliacao.produtos_sem_base,
            nota_mais_nova=max(datas.values(), default=None),
            hoje=hoje,
            apelidos=tabelas.apelidos,
            lojas=tabelas.lojas,
            insumos=banco.insumos(),
            fornecedores_boleto_conhecido=cfg.fornecedores_boleto_conhecido,
            notas_incompletas=leitura.incompletos,
            teste=teste,
        )
    )
    return _Rodada(relatorio, rodada.chaves, avaliacao.avisados)


def _salvar_previa(relatorio: Relatorio, segunda: date) -> tuple[Path, Path]:
    config.PASTA_LOCAL.mkdir(parents=True, exist_ok=True)
    texto = config.PASTA_LOCAL / f"{segunda.isoformat()}.txt"
    pagina = config.PASTA_LOCAL / f"{segunda.isoformat()}.html"
    texto.write_text(relatorio.texto, encoding="utf-8", newline="\n")
    pagina.write_text(relatorio.html, encoding="utf-8", newline="\n")
    return texto, pagina


def _segunda(semana: str | None, hoje: date) -> date:
    return segunda_da_semana(date.fromisoformat(semana)) if semana else semana_anterior(hoje)


def _ensaio(semana: str | None) -> int:
    """Mostra e salva a prévia. Não envia nem grava estado."""
    hoje = _hoje()
    segunda = _segunda(semana, hoje)
    rodada = _montar_rodada(config.carregar_config(), segunda, hoje)
    texto, pagina = _salvar_previa(rodada.relatorio, segunda)
    print(f"Assunto: {rodada.relatorio.assunto}\n\n{rodada.relatorio.texto}")
    print(f"(Ensaio: nada enviado. Prévia em {texto} e {pagina}.)")
    return 0


def _registrar_falha(comando: str, mensagem: str) -> None:
    """Guarda o motivo em disco: a janela some quando alguém a fecha, e a saída da tarefa não fica em lugar nenhum."""
    linha = f"{datetime.now():%d/%m/%Y %H:%M:%S} | {comando} | {' '.join(mensagem.split())}\n"
    with contextlib.suppress(OSError):
        config.PASTA_LOCAL.mkdir(parents=True, exist_ok=True)
        with (config.PASTA_LOCAL / ARQUIVO_DE_FALHAS).open("a", encoding="utf-8", newline="\n") as f:
            f.write(linha)


def _falhou(comando: str, mensagem: str, titulo: str | None) -> int:
    """Mostra, registra e, se houver título, avisa na tela. Devolve o código de saída da falha."""
    print(mensagem, file=sys.stderr)
    _registrar_falha(comando, mensagem)
    if titulo:
        aviso_na_tela(titulo, mensagem)
    return 1


def _tirar_trava(trava: Path) -> None:
    # Se não apagar, a próxima rodada da semana para e avisa: erra para o lado de não repetir o e-mail.
    with contextlib.suppress(OSError):
        trava.unlink(missing_ok=True)


def _enviar(semana: str | None, teste: bool, reenviar: bool, comando: str) -> int:
    """Manda o e-mail. O estado só é gravado depois de ele sair, e nunca no teste."""
    cfg = config.carregar_config()
    hoje = _hoje()
    segunda = _segunda(semana, hoje)
    periodo = intervalo(segunda.isoformat(), (segunda + timedelta(days=6)).isoformat())
    trava = trava_do_envio(segunda)
    if not teste and not reenviar:
        if ja_enviada(carregar_estado(), segunda):
            print(f"A semana {periodo} já foi enviada; nada a fazer. Com --reenviar, manda de novo.")
            return 0
        if trava.exists():
            return _falhou(
                comando,
                f"O e-mail {periodo} pode ter saído numa rodada anterior, sem registro. Nada foi enviado agora. "
                f"Se não chegou, apague {trava} e rode de novo.",
                _TITULO_DO_ALERTA,
            )

    rodada = _montar_rodada(cfg, segunda, hoje, teste, reenviar)
    texto, _ = _salvar_previa(rodada.relatorio, segunda)
    extras = () if teste else cfg.destinatarios_extras
    if not teste:
        # Sem a trava gravada, nada sai: um erro aqui para a rodada antes do envio.
        trava.parent.mkdir(parents=True, exist_ok=True)
        trava.write_text(f"{datetime.now():%d/%m/%Y %H:%M:%S}\n", encoding="utf-8")
    try:
        envio = enviar_email(cfg.vps_ssh, cfg.arquivo_env_remoto, rodada.relatorio, extras)
    except ErroDeEnvio as e:
        if e.talvez_enviado:
            depois = "" if teste else " A próxima rodada desta semana não manda outro."
            mensagem = f"O e-mail pode ter saído ({e}). Confira se chegou.{depois} Relatório em {texto}."
            return _falhou(comando, mensagem, _TITULO_DA_FALHA)
        if not teste:
            _tirar_trava(trava)
        return _falhou(comando, f"O e-mail não saiu ({e}). O relatório está em {texto}.", _TITULO_DA_FALHA)
    print(envio.resposta)

    avisos: list[str] = []
    if envio.recusados:
        avisos.append(f"O Gmail recusou {', '.join(envio.recusados)}: não recebeu.")
    if not teste:
        try:
            gravar_estado(registrar_envio(carregar_estado(), segunda, rodada.chaves, rodada.avisados))
        except (ErroDeEstado, OSError) as e:
            avisos.append(
                f"O e-mail já saiu, mas o estado não foi gravado ({type(e).__name__}). "
                "A próxima rodada desta semana não manda outro."
            )
        else:
            _tirar_trava(trava)
    if avisos:
        return _falhou(comando, " ".join(avisos), _TITULO_DO_ALERTA)
    return 0


def main(argumentos: list[str] | None = None) -> int:
    if isinstance(sys.stdout, io.TextIOWrapper):  # no Windows, o padrão não é UTF-8
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="alerta_preco", description="Relatório semanal de altas de preço.")
    parser.add_argument("--semana", metavar="AAAA-MM-DD", help="a semana desse dia; sem isto, a semana passada")
    parser.add_argument(
        "--conferir-leitura", type=Path, metavar="ARQUIVO", help="compara a leitura com a do JavaScript"
    )
    parser.add_argument("--simular", nargs=2, metavar=("DE", "ATE"), help="repete as rodadas de um período, sem enviar")
    parser.add_argument("--email", action="store_true", help="manda o e-mail; sem isto, é só um ensaio")
    parser.add_argument("--teste", action="store_true", help="com --email: marcado como teste, sem extras e sem estado")
    parser.add_argument("--reenviar", action="store_true", help="com --email: manda de novo uma semana já enviada")
    args = parser.parse_args(argumentos)
    if (args.teste or args.reenviar) and not args.email:
        print("--teste e --reenviar só valem com --email.", file=sys.stderr)
        return 1
    comando = " ".join(sys.argv[1:] if argumentos is None else argumentos) or "(ensaio)"
    try:
        if args.conferir_leitura:
            return _conferir_leitura(args.conferir_leitura)
        if args.simular:
            return _simular(*args.simular)
        if args.email:
            return _enviar(args.semana, args.teste, args.reenviar, comando)
        return _ensaio(args.semana)
    except (config.ErroDeConfiguracao, ErroDeFonte, ErroDeEstado, ErroDeLeitura, OSError, ValueError) as e:
        mensagem = str(e)
    except Exception as e:  # nenhum erro passa sem aviso; só o tipo, porque o texto dele pode trazer conteúdo
        mensagem = f"Erro inesperado ({type(e).__name__}). Nada foi enviado."
    return _falhou(comando, mensagem, _TITULO_DA_FALHA if args.email else None)


if __name__ == "__main__":
    raise SystemExit(main())
