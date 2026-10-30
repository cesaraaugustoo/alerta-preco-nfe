# Segurança

## Onde mora cada segredo

| Segredo | Onde fica | Quem o lê |
|---|---|---|
| Chave de leitura do banco | Arquivo apontado por `fontes.arquivo_env`, na máquina que roda o programa | `fontes.py`, em tempo de execução, só as duas variáveis que usa |
| Tokens do serviço de notas | No banco | `fontes.py`, em memória, durante a rodada |
| Senha do e-mail | Arquivo apontado por `envio.arquivo_env_remoto`, só no servidor | O programa curto que roda no servidor. A senha nunca chega à máquina local |
| Acesso ao servidor | Chave SSH do usuário | O cliente `ssh`, sempre com `BatchMode=yes`, sem pedir senha |

## O que nunca entra no repositório

- `config.toml`, o arquivo de segredos, o estado e as prévias: ficam em `~/alerta-preco` ou nos caminhos da configuração.
- XML real, CNPJ, razão social, nome de fornecedor ou de loja, preços reais, e-mails, endereços de servidor e caminhos de máquina.

Os testes usam só dados inventados: um número novo em teste, fixture ou documentação tem de ficar fora dos valores reais.

## As duas varreduras

**Antes de cada envio ao repositório, as duas varreduras têm de zerar, em todo o histórico: conteúdo, mensagens, autores e referências.** Sem
isso, não se envia. As duas mostram só contagens e ficam fora do repositório, porque os termos e os números que elas conhecem são dado sigiloso.

| Varredura | O que procura |
|---|---|
| De termos | Nomes de empresa, fornecedor, loja e produto, CNPJ, e-mails, endereços de servidor, caminhos de máquina e números de 14 ou 44 dígitos |
| De números | Todo número do histórico contra os preços, totais e quantidades reais das notas e dos resultados das rodadas |

A primeira sozinha não basta: ela só enxerga palavras, e um preço real num teste passa por ela sem nome nenhum ao lado. Cada coincidência da
segunda só é aceita com o motivo escrito, número a número e arquivo a arquivo. Por exemplo, um limite da regra ou um código HTTP. Na dúvida, o
número é trocado.

## Garantias do código

- **Só leitura:** o banco e o serviço de notas recebem só `GET`, e a pasta de notas nunca é escrita.
- **Erros sem segredo:** as mensagens dizem o que falhou e o código HTTP, ou o tipo do erro, sem o pedido nem o conteúdo. `Credenciais` e
  `ConfigEspiao` escondem os segredos no `repr`.
- **Conteúdo fora da linha de comando:** o relatório vai ao servidor pela entrada padrão, e o aviso na tela recebe o texto por variável de
  ambiente, sem passar pelo comando.
- **Erro inesperado sem conteúdo:** o aviso traz só o tipo do erro, porque o texto dele pode carregar dados.
- **Texto do XML é escapado:** o que vem da nota é tratado como texto no HTML do relatório.
- **Teste não chega a quem não deve:** `--teste` ignora os destinatários extras.

## Como relatar um problema

Abra uma [issue](https://github.com/cesaraaugustoo/alerta-preco-nfe/issues) dizendo só que há um problema de segurança, sem o detalhe e sem copiar
nenhum dado, e o autor combina ali como receber o resto. Vale também para dado real encontrado no repositório ou no histórico.
