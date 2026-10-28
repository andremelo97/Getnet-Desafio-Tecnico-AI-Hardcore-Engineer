"""Fixed texts the system says without a model: refusal, ticket line, fallbacks.

They exist in Portuguese, Spanish and English. Unknown languages fall back to Portuguese,
the language of Getnet's Brazilian merchants.
"""

DEFAULT_LANGUAGE = "pt"

REFUSAL = {
    "pt": (
        "Desculpe, não posso ajudar com esse pedido. Posso responder dúvidas sobre os "
        "produtos da Getnet e sobre a sua conta de lojista: vendas, recebimentos, Pix e "
        "maquininhas."
    ),
    "es": (
        "Lo siento, no puedo ayudar con esa solicitud. Puedo responder preguntas sobre los "
        "productos de Getnet y sobre tu cuenta de comercio: ventas, depósitos, Pix y "
        "terminales de pago."
    ),
    "en": (
        "Sorry, I can't help with that request. I can answer questions about Getnet's "
        "products and about your merchant account: sales, deposits, Pix and card machines."
    ),
}

TICKET_LINE = {
    "pt": "Protocolo de atendimento: {ticket_id}.",
    "es": "Número de protocolo: {ticket_id}.",
    "en": "Ticket number: {ticket_id}.",
}

ESCALATION_FALLBACK = {
    "pt": (
        "Encaminhei o seu caso para um especialista da Getnet, que vai dar continuidade ao "
        "atendimento."
    ),
    "es": "He enviado tu caso a un especialista de Getnet, que continuará con la atención.",
    "en": "I have forwarded your case to a Getnet specialist, who will take it from here.",
}

FALLBACK_OPERATOR_SUMMARY = (
    "Encaminhamento automático: o agente de escalonamento não respondeu. Origem: {origin}. "
    "Mensagem do lojista: {message} Motivo: {reason}"
)


def text_for(table: dict[str, str], language: str) -> str:
    return table.get(language.lower()[:2], table[DEFAULT_LANGUAGE])
