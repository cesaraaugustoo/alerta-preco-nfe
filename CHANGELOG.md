# Histórico de mudanças

O formato segue o [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/), e as versões seguem o
[Versionamento Semântico](https://semver.org/lang/pt-BR/).

## [0.1.0] - 2026-10-30

### Adicionado

- Leitura do XML da NF-e e da pasta de notas.
- Regra de alertas, com os filtros de nota e a comparação pela mediana das últimas compras.
- Leitura do banco, do serviço de notas e das tabelas locais.
- Estado das rodadas, com a nota atrasada e a não repetição de avisos.
- Relatório em texto e HTML, e o ensaio da semana com prévia salva.
- Envio do e-mail por SSH, com `--email`, `--teste` e `--reenviar`, e aviso na tela quando ele não sai.
- `fornecedores_boleto_conhecido` na configuração local: o fornecedor cujo boleto difere dos itens de forma permanente vira só uma
  contagem no rodapé, em vez de um aviso toda semana. A nota continua fora do cálculo de preço.

### Alterado

- Produto que estreia na rodada como troca: todas as notas dele na rodada usam a mesma base da primeira. Antes, a segunda nota era
  comparada com a estreia, e o valor mostrado cobria só parte do que a troca custou. Como as notas agora somam, uma estreia em notas
  pequenas pode passar do mínimo e virar alerta. Detalhes em [`docs/regra.md`](docs/regra.md).
- Assunto e primeira linha do relatório separam o que subiu do que trocou. O valor das trocas não entra no total de altas.
- Item cujo código já foi comprado em outra unidade fica "sem base", sem comparar pela matéria-prima, que poria caixa contra unidade.

### Corrigido

- O relatório não diz mais "nenhuma alta" sem ter lido as notas: pasta de ano ausente, arquivo que não abre ou semana sem nenhuma nota
  param a rodada, sem e-mail e com aviso na tela.
- Todo erro da rodada com `--email` gera aviso na tela e código de saída 1, inclusive os inesperados.
- XML cortado não é mais tratado como bonificação nem marcado como avaliado: o arquivo é contado e aparece no e-mail, e entra na conta
  quando for baixado de novo.
- Se o e-mail sai e a gravação do estado falha, o aviso diz que ele já saiu e como evitar um segundo envio.
- Destinatário recusado pelo servidor de e-mail não conta mais como enviado, e o aviso diz qual endereço foi recusado.
- O aviso de falha também abre uma janela que fica na tela até alguém fechar: a faixa do Windows dura segundos e pode passar despercebida.
- Tabela de regras, de matérias-primas ou de configuração que vem vazia do banco para a rodada: a chave ou o acesso mudou, e a conta sairia
  errada sem nenhum erro.
- Resposta malformada do banco interrompe a rodada com uma mensagem clara; a do serviço de notas vira aviso no e-mail.
- Próxima página vazia ou repetida no serviço de notas não prende mais a rodada em laço.
