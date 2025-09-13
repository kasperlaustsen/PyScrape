# -*- coding: utf-8 -*-
"""
Created on Wed Sep  3 13:18:27 2025

@author: KALSE
"""

from boligportal_collect_urls2 import get_city_listing_urls
urls = get_city_listing_urls("Horsens", headless=False, max_pages=100)
len(urls), urls[:5]