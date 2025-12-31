from baseBot import BaseBot
from dotenv import load_dotenv
import logging
import asyncio
from selenium import webdriver
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import re

logger = logging.getLogger(__name__)

class MarblesOnlyBot(BaseBot):
    def __init__(self, stream_name: str = "Feer"):
        super().__init__(
            overlay_ws_url=None,  # No overlay needed
            prefix='!',
            channel_name=stream_name,
            require_client_id=False  # Don't need API credentials for web scraping
        )
        self.stream_name = stream_name
        self.viewer_count_task = None
        self.driver = None
        self._init_browser()
    
    def _init_browser(self):
        """Initialize headless Firefox browser."""
        try:
            options = Options()
            options.add_argument("--headless")
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.set_preference("dom.webdriver.enabled", False)
            options.set_preference("useAutomationExtension", False)
            # Set user agent to avoid detection
            options.set_preference("general.useragent.override", 
                                 "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0")
            
            self.driver = webdriver.Firefox(options=options)
            self.driver.set_page_load_timeout(30)
            logger.info("Firefox browser initialized")
        except Exception as e:
            logger.error(f"Failed to initialize browser: {e}")
            self.driver = None
    
    async def get_viewer_count(self, stream_name: str = "Feer"):
        """Get current viewer count by scraping Twitch page."""
        if not self.driver:
            logger.error("Browser not initialized")
            return None
        
        url = f"https://www.twitch.tv/{stream_name}"
        
        # Run browser operations in thread pool (Selenium is synchronous)
        loop = asyncio.get_event_loop()
        try:
            viewer_count = await loop.run_in_executor(None, self._scrape_viewer_count, url)
            return viewer_count
        except Exception as e:
            logger.error(f"Error getting viewer count: {e}")
            return None
    
    def _scrape_viewer_count(self, url: str):
        """Synchronously scrape viewer count from Twitch page."""
        try:
            self.driver.get(url)
            
            # Wait for page to load and try multiple selectors
            wait = WebDriverWait(self.driver, 10)
            
            # Try different selectors for viewer count
            selectors = [
                '[data-a-target="animated-channel-viewers-count"]',
                '[data-a-target="channel-viewers-count"]',
                'p[data-a-target="animated-channel-viewers-count"]',
                'span[data-a-target="animated-channel-viewers-count"]',
            ]
            
            viewer_count = None
            
            for selector in selectors:
                try:
                    element = wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, selector)))
                    text = element.text.strip()
                    # Extract number from text (handles formats like "1.2K", "1.2M", "123")
                    viewer_count = self._parse_viewer_count(text)
                    if viewer_count is not None:
                        logger.debug(f"Found viewer count using selector {selector}: {text} -> {viewer_count}")
                        return viewer_count
                except (TimeoutException, NoSuchElementException):
                    continue
            
            # Fallback: try to find in page source
            page_source = self.driver.page_source
            # Look for viewer count in various formats
            patterns = [
                r'"viewerCount":(\d+)',
                r'"viewers":(\d+)',
                r'viewerCount["\']?\s*[:=]\s*(\d+)',
            ]
            
            for pattern in patterns:
                match = re.search(pattern, page_source)
                if match:
                    viewer_count = int(match.group(1))
                    logger.debug(f"Found viewer count in page source: {viewer_count}")
                    return viewer_count
            
            logger.warning(f"Could not find viewer count on page for {url}")
            return 0  # Stream might be offline
            
        except Exception as e:
            logger.error(f"Error scraping viewer count: {e}")
            return None
    
    def _parse_viewer_count(self, text: str):
        """Parse viewer count from text (handles K, M suffixes)."""
        if not text:
            return None
        
        # Remove commas and spaces
        text = text.replace(',', '').replace(' ', '').upper()
        
        # Try to extract number
        match = re.search(r'([\d.]+)([KM]?)', text)
        if match:
            number = float(match.group(1))
            suffix = match.group(2)
            
            if suffix == 'K':
                return int(number * 1000)
            elif suffix == 'M':
                return int(number * 1000000)
            else:
                return int(number)
        
        return None
    
    async def _viewer_count_loop(self):
        """Print viewer count every minute."""
        while True:
            try:
                viewer_count = await self.get_viewer_count(self.stream_name)
                if viewer_count is not None:
                    status = "LIVE" if viewer_count > 0 else "OFFLINE"
                    print(f"{self.stream_name}: {viewer_count} viewers ({status})")
                else:
                    print(f"{self.stream_name}: Unable to get viewer count")
                await asyncio.sleep(15)  # Wait 1 minute
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in viewer count loop: {e}", exc_info=True)
                await asyncio.sleep(60)
    
    async def event_ready(self):
        """Called when bot is ready."""
        logger.info(f'MarblesOnlyBot logged in as {self.nick}')
        if self.driver:
            self.viewer_count_task = asyncio.create_task(self._viewer_count_loop())
        else:
            logger.error("Browser not initialized, cannot start viewer count monitoring")
    
    async def stop(self):
        """Clean up browser."""
        if self.viewer_count_task:
            self.viewer_count_task.cancel()
            try:
                await self.viewer_count_task
            except asyncio.CancelledError:
                pass
        
        if self.driver:
            try:
                self.driver.quit()
                logger.info("Browser closed")
            except Exception as e:
                logger.error(f"Error closing browser: {e}")
    
    def __del__(self):
        """Clean up browser on destruction."""
        if self.driver:
            try:
                self.driver.quit()
            except:
                pass

if __name__ == "__main__":
    load_dotenv()
    bot = MarblesOnlyBot(stream_name="Xaryu")
    bot.run()
