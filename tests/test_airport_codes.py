from sources.airport_codes import airport_display_code, airport_position


def test_airports_are_found_by_icao_code_or_faa_location_id():
    assert airport_position('KPHL') == airport_position(' phl ')
    assert airport_display_code('PHL') == 'PHL'
    # Blairstown has no ICAO or IATA code, only the FAA id 1N7.
    lat, lon = airport_position('1N7')
    assert (round(lat), round(lon)) == (41, -75)
    assert airport_display_code('1N7') == '1N7'
    assert airport_position('ZZZZ') is None
    assert airport_position(None) is None
