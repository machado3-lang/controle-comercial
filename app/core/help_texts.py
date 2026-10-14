"""
Textos de ajuda dos campos do sistema (o "?" ao lado do label).

Fonte unica de verdade: o texto vive aqui, e nao dentro dos 90+ templates, para
poder reviser, versionar e reaproveitar. A chave segue `tela.campo` e e a mesma
usada na chamada `{{ ajuda('os.equipamento') }}` do template.

Regra de ouro do componente: chave inexistente nao quebra a tela — `ajuda()`
devolve string vazia e o botão simplesmente não aparece.

Texto curto (1 a 3 frases). "Como fazer" (passo a passo da tela) não cabe aqui:
esse conteúdo vive nos DOCUMENTACAO_*.md do repo.
"""

TEXTOS_AJUDA = {
    # ---- Ordem de Serviço ----
    "os.cliente": (
        "Cliente que é dono do equipamento e que receberá a cobrança. A busca aceita "
        "nome ou CPF/CNPJ; o cadastro precisa existir antes."
    ),
    "os.equipamento": (
        "Descrição do aparelho que entrou para manutenção. É o campo que aparece "
        "primeiro na OS impressa e na tela de atendimento."
    ),
    "os.marca": "Fabricante do equipamento. Ajuda nas estatísticas de_OS e no histórico técnico.",
    "os.modelo": "Modelo/referência do aparelho. Ex.: \"Latitude 5420\".",
    "os.numero_serie": (
        "Número de série do equipamento que vai ser reparado — é este aparelho que "
        "a OS identifica. Localize a etiqueta física: é o que distingue dois "
        "aparelhos idênticos do mesmo cliente."
    ),
    "os.tecnico": "Quem executou o serviço. Aparece no impresso e no histórico da OS.",
    "os.autorizado_por": (
        "Nome de quem autorizou o serviço. Em garantia, costuma ser o próprio "
        "cliente: é o registro de que ele aprovou o orçamento."
    ),
    "os.numero_requisicao": (
        "Número da ordem de compra que o cliente emitiu autorizando este serviço. "
        "Muitos clientes (nem todos) geram uma OC e enviam junto do equipamento; "
        "quando existe, é ela que autoriza o reparo e o valor."
    ),
    "os.defeito_relatado": (
        "O que o cliente disse que estava errado, com as palavras dele. Fica no "
        "histórico técnico e não vai para a nota fiscal."
    ),
    "os.observacao": (
        "Anotação interna sobre o serviço. Aparece na OS e no impresso, mas não "
        "vai sozinha para a nota fiscal: isso depende da opção abaixo."
    ),
    "os.propagar_observacao": (
        "Define o padrão do botão \"Incluir nas notas\" que aparece na emissão da "
        "NFe e da NFSe desta OS. Desmarcado, a observação não vai para a nota. "
        "Depois que a nota é transmitida isso não muda mais: na NFe precisaria de "
        "carta de correção."
    ),
    "os.status": (
        "Andamento da OS. As transições são controladas: Aberta → Em andamento → "
        "Finalizada → Concluída, e Concluída pode ser cancelada. Emitir nota exige "
        "a OS concluída."
    ),
    "os.data_entrada": "Data em que o equipamento entrou. Aparece no recibo e no histórico.",
    "os.data_saida": (
        "Data prevista ou realizada da entrega. É a base do vencimento quando a "
        "cobrança é gerada sem primeiro vencimento informado."
    ),
    "os.valor_servico": (
        "Valor da mão de obra. É o que vira NFS-e; peças viram NFe."
    ),
    "os.valor_pecas": (
        "Valor total das peças. É o que vira NFe; a mão de obra vira NFS-e."
    ),
    "os.cobranca_separada": (
        "Ligado, a OS gera uma cobrança por nota (uma da NFe e outra da NFSe). "
        "Desligado, gera uma única cobrança agrupando as duas, com um boleto só."
    ),
    "os.forma_pagamento": (
        "Condição de pagamento da OS. Fica salva e é oferecida como padrão nas "
        "telas de emissão da NFe e da NFSe. Recibo sem nota (PIX, dinheiro) e "
        "garantia não sobrescrevem este valor."
    ),
    "os.num_parcelas": "Em quantas parcelas a cobrança é dividida. Boletos de parcelas diferentes são emitidos separadamente.",
    "os.intervalo_dias": "Dias entre o vencimento de uma parcela e o da seguinte.",
    "os.primeiro_vencimento": "Vencimento da 1ª parcela. Vazio usa a data de saída da OS.",
}


def texto_ajuda(chave: str):
    """Texto de ajuda da chave, ou None se ela nao existir."""
    return TEXTOS_AJUDA.get(chave)