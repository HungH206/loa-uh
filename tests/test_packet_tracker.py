from backend.network_stats import packet_summary


def message(sequence, boot='boot-a', source='mqtt'):
    return dict(sequence=sequence, boot_id=boot, source=source, received_at=100)


def test_gaps_are_separate_per_boot_and_late_arrivals_fill_them():
    rows = [message(10), message(13), message(12), message(13),
            message(0, 'boot-b'), message(1, 'boot-b')]
    result = packet_summary(rows)
    assert result['received'] == 5
    assert result['expected'] == 6
    assert result['missing'] == 1
    assert result['loss_percent'] == 16.67
    assert result['gaps'] == [dict(boot_id='boot-a', first=11, last=11, count=1)]
    assert packet_summary(rows + [message(11)])['missing'] == 0


def test_no_data_is_unknown_and_browser_or_unidentified_messages_are_excluded():
    result = packet_summary([message(1, source='browser'), message(2, boot=None), message(None)])
    assert result['loss_percent'] is None
    assert result['expected'] == 0
    assert result['recent'] == []


def test_large_gaps_are_ranges_and_receipts_are_bounded():
    rows = [message(i) for i in range(150)] + [message(1_000_000)]
    result = packet_summary(rows)
    assert result['missing'] == 999850
    assert len(result['recent']) == 100
    assert result['recent'][0]['sequence'] == 1_000_000
    assert len(result['gaps']) == 1
