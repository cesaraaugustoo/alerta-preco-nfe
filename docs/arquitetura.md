# Arquitetura

## Módulos

| Módulo | O que faz | Toca em |
|---|---|---|
| `__main__.py` | Linha de comando e orquestração da rodada | Tudo abaixo |
| `config.py` | Os limites da regra, com o motivo, e a leitura de `config.toml` | Disco |
| `modelos.py` | As dataclasses compartilhadas | Nada |
| `xml_nota.py` | Lê a pasta e o XML da NF-e | Disco, só leitura |
| `fontes.py` | Banco, serviço de notas e tabelas em Markdown | Rede e disco, só leitura |
| `regra.py` | Filtros e alertas | Nada: funções puras |
| `relatorio.py` | Assunto, texto e HTML | Nada: funções puras |
| `estado.py` | O que já foi avaliado e avisado | Disco, só o arquivo de estado |
| `envio.py` | E-mail por SSH e aviso na tela | `ssh` e PowerShell |

## O fluxo de uma rodada

Com `--email`, nesta ordem:

1. Lê a configuração e o estado. Se a semana já foi enviada, para sem erro, para a tarefa agendada poder repetir sem mandar outro e-mail.
2. Lê o XML dos 120 dias anteriores à semana, que formam a base. A leitura vai até hoje, para o rodapé dizer até onde a pasta chegou. Faltou a pasta
   de um ano ou algum arquivo não abriu: a rodada para aqui.
3. Separa as notas da rodada: as da semana e as atrasadas, que chegaram depois de a semana delas ter sido avaliada, até 14 dias antes. Nenhuma nota:
   a rodada para aqui.
4. Consulta as notas canceladas no banco e no serviço de notas. Falha no serviço de notas vira aviso no relatório; falha no banco, nos arquivos ou
   na configuração para a rodada.
5. Filtra as notas, calcula as linhas de preço e avalia os alertas.
6. Monta o relatório e salva a prévia.
7. Envia. Só depois de o e-mail sair, grava o estado. Se a gravação falhar ou um destinatário for recusado, o aviso na tela diz o que aconteceu, e a
   saída é 1.

## Por que cada escolha

**Só biblioteca padrão.** A rotina roda numa máquina de uso diário, e o programa não pode depender de pacote instalado. `ruff`, `mypy` e
`coverage` rodam só no CI.

**Expressões regulares em vez de parser de XML.** O programa acompanha um leitor em JavaScript que continua em uso, e os dois têm de chegar ao
mesmo número. As fixtures em `tests/fixtures` travam isso, com a saída esperada gerada pelo próprio JavaScript, e `--conferir-leitura` faz a
mesma comparação numa pasta real, sem levar nada para o repositório. Por isso `xml_nota.py` imita o JavaScript onde o Python faria diferente:
`parseFloat`, `trim`, `Math.round` e a soma sem compensação.

**Mediana em vez de média ou última compra.** Um preço errado isolado não move a mediana nem vira o novo normal.

**Regra e relatório como funções puras.** Sem rede e sem disco, os dois são testados com dados inventados nos valores exatos dos limites, e
`--simular` repete a regra sobre um período inteiro para comparar com o resultado esperado.

**Estado gravado depois do envio.** Se o e-mail falha, a próxima execução tenta de novo. O arquivo é escrito num temporário e trocado de uma
vez, e um estado ilegível para a rodada, em vez de ser tratado como vazio, porque isso reenviaria avisos antigos.

**Falha de fonte para a rodada.** Sem as regras de conversão ou as notas canceladas, a conta sairia errada e pareceria certa. É melhor não mandar
e-mail e avisar na tela do que mandar um relatório enganoso. O mesmo vale para a pasta de notas: um "nenhuma alta" só pode sair depois de lidas
todas as notas. A exceção é a pasta do ano seguinte, que pode não existir no começo de janeiro.

**XML cortado é tratado à parte do leitor.** `lerNotaXml` não percebe um arquivo cortado, e o porte tem de dar a mesma conta do JavaScript. Por isso a
verificação fica em `nota_incompleta`, fora de `ler_nota_xml`. O arquivo cortado não entra na conta nem é marcado como avaliado, então volta sozinho
quando for baixado de novo, dentro da janela de atraso.

**Erro inesperado também avisa.** O agendador não mostra a saída do programa. Sem o aviso na tela, uma exceção não prevista passaria sem ninguém
saber que a rodada não saiu.

**Faixa e janela no aviso.** A faixa do Windows dura segundos e pode passar despercebida, como aconteceu no primeiro teste. A janela fica aberta até
alguém fechar e roda como processo à parte: não prende o programa nem conta no limite de tempo da tarefa.

**O envio passa pelo servidor.** A senha do e-mail só existe no servidor e não vem para a máquina local. Um programa curto, enviado pelo `ssh`, lê
o arquivo de segredos lá e recebe o relatório pela entrada padrão. A resposta dele é só em ASCII: um erro de acento depois do envio faria um
e-mail que saiu parecer falha, e a próxima tentativa o repetiria.

**A chave do banco fica na máquina local.** As regras de conversão exigem essa chave, e ela não vai para o servidor. Por isso o cálculo roda
localmente e só o relatório pronto é enviado.

**Dados só em tempo de execução.** As tabelas de apelidos, de lojas e de notas fora são lidas de Markdown fora do repositório, e o código não
conhece nenhum nome real. Veja [`SECURITY.md`](../SECURITY.md).
