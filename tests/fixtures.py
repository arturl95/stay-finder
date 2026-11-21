"""Hand-made minimal versions of the JSON Airbnb embeds in its pages. Only the fields the parser
reads are present; all names, texts and numbers are invented."""
import base64


def gid(num):
    return base64.b64encode(f"DemandStayListing:{num}".encode()).decode()


def search_result(num, name, lat, lng, price="€420", qualifier="for 7 nights", discounted=None):
    line = {"qualifier": qualifier}
    if discounted:
        line.update(discountedPrice=discounted, originalPrice=price)
    else:
        line["price"] = price
    return {
        "__typename": "StaySearchResult",
        "nameLocalized": {"localizedStringWithTranslationPreference": name},
        "structuredDisplayPrice": {"primaryLine": line},
        "demandStayListing": {"id": gid(num), "location": {"coordinate": {"latitude": lat, "longitude": lng}}},
    }


def search_state(results, count=None, cursors=("c0",)):
    return {"niobeClientData": [["StaysSearch:{}", {"data": {"presentation": {"staysSearch": {"results": {
        "searchResults": results,
        "paginationInfo": {"pageCursors": list(cursors)},
        "filters": {"filterPanel": {"resultCount": len(results) if count is None else count}},
    }}}}}]]}


def listing_state(description="A quiet flat with a small balcony.", rules_text="No parties. Pets are welcome.",
                  original="", rating=4.87, reviews=12, photos=None, amenities=None):
    photos = photos if photos is not None else [("Living room", "https://img.example/1.jpg"),
                                                ("Living room", "https://img.example/2.jpg"),
                                                ("Bedroom", "https://img.example/3.jpg")]
    stops = {}
    for room, uri in photos:
        stops.setdefault(room, []).append({"image": {"uri": uri}})
    amenities = amenities if amenities is not None else ["Wifi", "Kitchen", "Free washer – In unit"]
    return {"niobeClientData": [["StaysPdpSections:{}", {"data": {"node": {"pdpPresentation": {
        "sharingConfig": {"propertyType": "Entire rental unit"},
        "overview": {"items": ["4 guests", "1 bedroom", "2 beds", "1 bath"]},
        "quality": {
            "listingRatingStats": {"overallRatingStats": {"ratingAverage": rating if reviews else 0,
                                                          "ratingCount": reviews}},
            "isGuestFavorite": True,
            "categoryRatings": [{"categoryType": "CLEANLINESS", "localizedRating": "4.9"},
                                {"categoryType": "VALUE", "localizedRating": "4.5"}],
        },
        "mediaTour": {"stops": [{"name": k, "items": v} for k, v in stops.items()]},
        "heroMedia": {"edges": []},
        "descriptions": {"longDescriptionHtml": {
            "localizedStringWithTranslationPreference": f"<p>{description}</p>",
            "source": original}},
        "rules": {"groupItems": [{"items": [
            {"title": "Check-in after 3:00 PM"},
            {"title": "Up to 4 guests", "description": {"__typename": "Plain", "text": "max"}},
            {"title": "Additional rules", "description": {"__typename": "PdpUGCText", "content": {
                "localizedStringWithTranslationPreference": rules_text, "source": original}}},
        ]}]},
        "amenities": {"seeAllAmenitiesGroups": [
            {"title": "Basics", "amenities": [{"title": a, "available": True} for a in amenities]},
            {"title": "Not included", "amenities": [{"title": "Air conditioning", "available": False}]},
        ]},
        "hostInfo": {"passportData": {"isSuperhost": False}},
    }}}}]]}
