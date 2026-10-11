---
name: price-availability
title: Price, availability or specification check
triggers: how much does, price of, in stock, cheapest, specs of, availability, cost comparison, where to buy
examples:
  - "how much does the pro plan of a service cost per year"
  - "which stores have the console in stock"
  - "what are the specifications of a particular monitor"
applies_when: the user wants current numbers that change over time
---
**Done looks like:** the figures with the date checked and the seller/source for each; totals via the calculator.
**Approach:** `web_search` with product + "price" and the current year; open the official or retailer page (`web_fetch`, or the browser when the price is loaded by scripts); never quote a price from a snippet alone.
**Tools:** web_search; web_fetch; browser_open/browser_read for interactive stores; calculate for totals, taxes, conversions.
**Pitfalls:** regional prices and currencies; promotional vs list price; comparison sites showing stale data.
**Verify:** each figure comes from a page you read, with its date.
**Stop / ask:** if the user's region or currency is unknown and matters, ask.
