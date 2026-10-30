import json
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from alerta_preco.estado import (
    ErroDeEstado,
    Estado,
    avisados_da_rodada,
    carregar_estado,
    gravar_estado,
    ja_enviada,
    notas_da_rodada,
    registrar_envio,
    segunda_da_semana,
    semana_anterior,
    semanas_entre,
)

SEGUNDA = date(2026, 3, 16)


class TestSemanas(unittest.TestCase):
    def test_segunda_da_semana(self) -> None:
        self.assertEqual(segunda_da_semana(date(2026, 3, 16)), SEGUNDA)
        self.assertEqual(segunda_da_semana(date(2026, 3, 22)), SEGUNDA)

    def test_semana_anterior_a_partir_de_qualquer_dia(self) -> None:
        self.assertEqual(semana_anterior(date(2026, 3, 16)), date(2026, 3, 9))
        self.assertEqual(semana_anterior(date(2026, 3, 22)), date(2026, 3, 9))

    def test_semanas_entre_duas_datas_cortam_na_data_final(self) -> None:
        self.assertEqual(
            semanas_entre(date(2026, 3, 4), date(2026, 3, 18)),
            [("2026-03-02", "2026-03-08"), ("2026-03-09", "2026-03-15"), ("2026-03-16", "2026-03-18")],
        )


class TestPeriodo(unittest.TestCase):
    NOTAS = (
        ("anterior_a_janela", "2026-02-28"),
        ("atrasada", "2026-03-05"),
        ("ja_avaliada", "2026-03-06"),
        ("da_semana", "2026-03-16"),
        ("fim_da_semana", "2026-03-22"),
        ("depois", "2026-03-23"),
    )

    def test_sem_estado_olha_so_a_semana(self) -> None:
        rodada = notas_da_rodada(self.NOTAS, SEGUNDA, None)
        self.assertEqual(rodada.chaves, {"da_semana", "fim_da_semana"})
        self.assertEqual(rodada.atrasadas, set())

    def test_com_estado_pega_a_nota_atrasada_dos_14_dias(self) -> None:
        estado = Estado(semanas_enviadas=["2026-02-23"], notas_avaliadas={"ja_avaliada"})
        rodada = notas_da_rodada(self.NOTAS, SEGUNDA, estado)
        self.assertEqual(rodada.chaves, {"atrasada", "da_semana", "fim_da_semana"})
        self.assertEqual(rodada.atrasadas, {"atrasada"})

    def test_primeiros_14_dias_nao_viram_atraso_antes_da_primeira_rodada(self) -> None:
        estado = Estado(semanas_enviadas=["2026-03-09"], notas_avaliadas=set())
        rodada = notas_da_rodada(self.NOTAS, SEGUNDA, estado)
        self.assertEqual(rodada.atrasadas, set())

    def test_reenvio_reavalia_a_semana_mesmo_ja_avaliada(self) -> None:
        estado = Estado(semanas_enviadas=["2026-03-16"], notas_avaliadas={"da_semana", "fim_da_semana"})
        self.assertEqual(notas_da_rodada(self.NOTAS, SEGUNDA, estado).chaves, {"da_semana", "fim_da_semana"})


class TestEnvio(unittest.TestCase):
    def test_sem_estado_nada_foi_enviado(self) -> None:
        self.assertFalse(ja_enviada(None, SEGUNDA))

    def test_semana_repetida_e_reconhecida(self) -> None:
        self.assertTrue(ja_enviada(Estado(semanas_enviadas=["2026-03-16"]), SEGUNDA))
        self.assertFalse(ja_enviada(Estado(semanas_enviadas=["2026-03-09"]), SEGUNDA))

    def test_registrar_acumula_sem_mexer_no_original(self) -> None:
        original = Estado(semanas_enviadas=["2026-03-09"], notas_avaliadas={"a"}, ultimo_avisado={"p": 1.0})
        novo = registrar_envio(original, SEGUNDA, {"b"}, {"q": 2.0})
        self.assertEqual(novo.semanas_enviadas, ["2026-03-09", "2026-03-16"])
        self.assertEqual(novo.notas_avaliadas, {"a", "b"})
        self.assertEqual(novo.ultimo_avisado, {"p": 1.0, "q": 2.0})
        self.assertEqual(original.notas_avaliadas, {"a"})

    def test_registrar_sem_estado_comeca_um(self) -> None:
        self.assertEqual(registrar_envio(None, SEGUNDA, {"b"}, {}).semanas_enviadas, ["2026-03-16"])

    def test_reenviar_nao_repete_a_semana_na_lista(self) -> None:
        estado = registrar_envio(Estado(semanas_enviadas=["2026-03-16"]), SEGUNDA, set(), {})
        self.assertEqual(estado.semanas_enviadas, ["2026-03-16"])

    def test_rodada_normal_usa_o_ultimo_aviso(self) -> None:
        estado = Estado(ultimo_avisado={"p": 5.0})
        self.assertEqual(avisados_da_rodada(estado, SEGUNDA, reenviar=False), {"p": 5.0})
        self.assertEqual(avisados_da_rodada(None, SEGUNDA, reenviar=False), {})

    def test_reenviar_a_semana_nao_suprime_os_proprios_alertas(self) -> None:
        estado = registrar_envio(Estado(ultimo_avisado={"p": 5.0}), SEGUNDA, set(), {"p": 9.0, "q": 3.0})
        self.assertEqual(avisados_da_rodada(estado, SEGUNDA, reenviar=False), {"p": 9.0, "q": 3.0})
        self.assertEqual(avisados_da_rodada(estado, SEGUNDA, reenviar=True), {"p": 5.0})


class TestArquivo(unittest.TestCase):
    def setUp(self) -> None:
        pasta = tempfile.TemporaryDirectory()
        self.addCleanup(pasta.cleanup)
        self.arquivo = Path(pasta.name) / "sub" / "estado.json"

    def test_sem_arquivo_nao_ha_estado(self) -> None:
        self.assertIsNone(carregar_estado(self.arquivo))

    def test_grava_e_le_igual(self) -> None:
        estado = registrar_envio(None, SEGUNDA, {"b", "a"}, {"p": 153.33})
        gravar_estado(estado, self.arquivo)
        self.assertEqual(carregar_estado(self.arquivo), estado)

    def test_formato_do_arquivo(self) -> None:
        gravar_estado(Estado(semanas_enviadas=["2026-03-16"], notas_avaliadas={"b", "a"}, ultimo_avisado={"p": 1.72}), self.arquivo)
        dados = json.loads(self.arquivo.read_text(encoding="utf-8"))
        self.assertEqual(dados["semanas_enviadas"], ["2026-03-16"])
        self.assertEqual(dados["notas_avaliadas"], ["a", "b"])
        self.assertEqual(dados["ultimo_avisado"], {"p": 1.72})

    def test_falha_na_troca_mantem_o_arquivo_anterior(self) -> None:
        gravar_estado(Estado(semanas_enviadas=["2026-03-09"]), self.arquivo)
        with mock.patch("os.replace", side_effect=OSError("preso")), self.assertRaises(OSError):
            gravar_estado(Estado(semanas_enviadas=["2026-03-16"]), self.arquivo)
        estado = carregar_estado(self.arquivo)
        assert estado is not None
        self.assertEqual(estado.semanas_enviadas, ["2026-03-09"])
        self.assertEqual(os.listdir(self.arquivo.parent), ["estado.json"])

    def test_arquivo_corrompido_interrompe(self) -> None:
        self.arquivo.parent.mkdir()
        for texto in ("{ quebrado", '["lista"]', '{"semanas_enviadas": 3}'):
            self.arquivo.write_text(texto, encoding="utf-8")
            with self.assertRaisesRegex(ErroDeEstado, "estado.json"):
                carregar_estado(self.arquivo)


if __name__ == "__main__":
    unittest.main()
