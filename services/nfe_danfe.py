import os

from brazilfiscalreport.danfe import Danfe, DanfeConfig


def gerar_danfe_pdf(xml_string: str, output_path: str) -> str:
    logo_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logo.png"
    )
    config = DanfeConfig(
        logo=logo_path if os.path.exists(logo_path) else None
    )
    danfe = Danfe(xml=xml_string, config=config)
    danfe.output(output_path)
    return output_path
