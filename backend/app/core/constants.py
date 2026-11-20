MAX_QUESTION_LENGTH = 4000
DEFAULT_CATALOGUE_CURRENCY = "INR"
CATALOGUE_CURRENCY = DEFAULT_CATALOGUE_CURRENCY

# Standard ISO 4217 currency codes
ISO_4217_CURRENCY_CODES = frozenset({
    "AED", "AFN", "ALL", "AMD", "ANG", "AOA", "ARS", "AUD", "AWG", "AZN",
    "BAM", "BBD", "BDT", "BGN", "BHD", "BIF", "BMD", "BND", "BOB", "BRL",
    "BSD", "BTN", "BWP", "BYN", "BZD", "CAD", "CDF", "CHF", "CLP", "CNY",
    "COP", "CRC", "CUP", "CVE", "CZK", "DJF", "DKK", "DOP", "DZD", "EGP",
    "ERN", "ETB", "EUR", "FJD", "FKP", "FOK", "GBP", "GEL", "GGP", "GHS",
    "GIP", "GMD", "GNF", "GTQ", "GYD", "HKD", "HNL", "HRK", "HTG", "HUF",
    "IDR", "ILS", "IMP", "INR", "IQD", "IRR", "ISK", "JEP", "JMD", "JOD",
    "JPY", "KES", "KGS", "KHR", "KID", "KMF", "KRW", "KWD", "KYD", "KZT",
    "LAK", "LBP", "LKR", "LRD", "LSL", "LYD", "MAD", "MDL", "MGA", "MKD",
    "MMK", "MNT", "MOP", "MRU", "MUR", "MVR", "MWK", "MXN", "MYR", "MZN",
    "NAD", "NGN", "NIO", "NOK", "NPR", "NZD", "OMR", "PAB", "PEN", "PGK",
    "PHP", "PKR", "PLN", "PYG", "QAR", "RON", "RSD", "RUB", "RWF", "SAR",
    "SBD", "SCR", "SDG", "SEK", "SGD", "SHP", "SLE", "SLL", "SOS", "SRD",
    "SSP", "STN", "SYP", "SZL", "THB", "TJS", "TMT", "TND", "TOP", "TRY",
    "TTD", "TVD", "TWD", "TZS", "UAH", "UGX", "USD", "UYU", "UZS", "VES",
    "VND", "VUV", "WST", "XAF", "XCD", "XOF", "XPF", "YER", "ZAR", "ZMW",
    "ZWL",
})

CURRENCY_ALIASES: dict[str, frozenset[str]] = {
    "USD": frozenset({"USD", "$", "US DOLLAR", "US DOLLARS", "DOLLAR", "DOLLARS"}),
    "INR": frozenset({"INR", "₹", "RS", "RS.", "RUPEE", "RUPEES", "INDIAN RUPEE", "INDIAN RUPEES"}),
    "EUR": frozenset({"EUR", "€", "EURO", "EUROS"}),
    "GBP": frozenset({"GBP", "£", "POUND", "POUNDS", "BRITISH POUND", "BRITISH POUNDS"}),
    "CAD": frozenset({"CAD", "C$", "CANADIAN DOLLAR", "CANADIAN DOLLARS"}),
    "AUD": frozenset({"AUD", "A$", "AUSTRALIAN DOLLAR", "AUSTRALIAN DOLLARS"}),
    "JPY": frozenset({"JPY", "¥", "YEN", "JAPANESE YEN"}),
}


def get_supported_currency_aliases(currency_code: str) -> frozenset[str]:
    """Return the set of acceptable string aliases for a configured ISO currency."""
    code = currency_code.strip().upper()
    aliases = CURRENCY_ALIASES.get(code, frozenset())
    return frozenset({code} | aliases)


def get_currency_symbol(currency_code: str) -> str:
    """Return standard symbol for formatted display."""
    symbols = {
        "USD": "$",
        "INR": "₹",
        "EUR": "€",
        "GBP": "£",
        "JPY": "¥",
        "CAD": "C$",
        "AUD": "A$",
    }
    return symbols.get(currency_code.strip().upper(), f"{currency_code.strip().upper()} ")


SUPPORTED_CURRENCIES = get_supported_currency_aliases(DEFAULT_CATALOGUE_CURRENCY)

POLICY_UNAVAILABLE_MESSAGE = (
    "Store policy information (including return periods, refund policies, "
    "shipping timelines, and warranty terms) is not currently available. "
    "Please contact customer support for verified policy details."
)

CATALOGUE_UNAVAILABLE_MESSAGE = (
    "Our product catalogue is temporarily unavailable. Please try again later."
)
