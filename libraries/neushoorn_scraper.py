import requests
from bs4 import BeautifulSoup
import re
from datetime import datetime
import libraries.logger as logger


class Scraper:
    def __init__(self, debug: bool = False):
        self.url = "https://www.neushoorn.nl/programma"
        self.log = logger.file_logger()
        self.log.initialize("NHScraper")
        self.debug = debug

        self.months = {
            "jan": "01", "feb": "02", "mar": "03", "apr": "04",
            "may": "05", "jun": "06", "jul": "07", "aug": "08",
            "sep": "09", "oct": "10", "nov": "11", "dec": "12"
        }

    def _parse_date(self, date_str, year=2026):
        """
        Converts '27 Mei' → '2026-05-27'
        """
        match = re.match(r"(\d{1,2})\s+([a-zA-Z]+)", date_str.strip())
        if not match:
            return None

        day = int(match.group(1))
        month_text = match.group(2).lower()

        month = self.months.get(month_text[:3])
        if not month:
            return None

        return f"{year}-{month}-{day:02d}"

    def _scrape_event(self, url):
        html = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0"}
        ).text

        soup = BeautifulSoup(html, "html.parser")

        result = {
            "location": None,
            "doors_open": None,
            "start_show": None,
            "schedule": []
        }

        # -----------------
        # DETAILS BLOCKS
        # -----------------

        blocks = soup.select(".event-details_block")

        for block in blocks:

            title = block.select_one(".event-details_title")

            if not title:
                continue

            section = title.get_text(strip=True)

            if section == "Zaal":

                zalen = []

                zaal_items = block.select(
                    ".event-details_item .event-details_content"
                )

                for item in zaal_items:

                    text = item.get_text(strip=True)

                    if text:
                        zalen.append(text)

                result["location"] = zalen

            elif section == "Aanvang":

                rows = block.select(".event-details_inner")

                for row in rows:

                    values = row.select(".event-details_content")

                    if len(values) < 2:
                        continue

                    label = values[0].get_text(strip=True)
                    value = values[1].get_text(strip=True)

                    if "Deuren open" in label:
                        result["doors_open"] = value

                    elif "Aanvang" in label:
                        result["start_show"] = value

        # -----------------
        # TIJDSCHEMA
        # -----------------

        tijdschema = soup.select_one(".rich-text-tijdschema")

        if tijdschema:

            for p in tijdschema.find_all("p"):

                strong = p.find("strong")

                if not strong:
                    continue

                time_slot = strong.get_text(strip=True)

                # Remove the strong tag text from paragraph
                full_text = p.get_text(" ", strip=True)

                artist = full_text.replace(time_slot, "")
                artist = artist.replace("·", "")
                artist = artist.strip()

                result["schedule"].append({
                    "time": time_slot,
                    "artist": artist
                })

        self.log.info(f"Successfully scraped: {url}", self.debug)
        return result

    def get_program_data(self, target_date):
        self.log.info(f"Scraping for {target_date}")
        headers = {"User-Agent": "Mozilla/5.0"}
        html = requests.get(self.url, headers=headers).text
        soup = BeautifulSoup(html, "html.parser")

        results = []

        items = soup.select(".program_item")

        for item in items:

            # --- Title ---
            title_tag = item.select_one(".program_title")
            title = title_tag.get_text(strip=True) if title_tag else "Unknown"

            # --- Link (IMPORTANT FIX) ---
            link_tag = item.select_one(".program_item-link")
            if not link_tag or not link_tag.get("href"):
                continue

            link = link_tag["href"]
            full_link = "https://www.neushoorn.nl" + link

            # --- Date ---
            date_tag = item.select_one(".program_date")
            raw_date = date_tag.get_text(strip=True) if date_tag else None

            parsed_date = self._parse_date(raw_date, target_date.split("-")[0]) if raw_date else None
            if parsed_date > target_date:
                break
            if parsed_date == target_date:
                event_details = self._scrape_event(full_link)

                # --- Match ---
                results.append({
                    "title": title,
                    "date": parsed_date,
                    "link": full_link,
                    "event_details": event_details
                })

        self.log.info(f"Successfully scraped: {self.url} for date {target_date}", self.debug)
        return results
