import libraries.neushoorn_scraper as scraper
import pprint

scrp = scraper.Scraper()

data = scrp.get_program_data("2026-05-28")

pprint.pprint(data)