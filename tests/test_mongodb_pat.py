import pytest
from datetime import datetime, timezone, timedelta
from mongo.pat import PersonalAccessToken


def test_mongodb_pat_integration():
    """Test basic PAT functionality with MongoDB"""

    # Clean up any existing test data
    PersonalAccessToken.objects(pat_id__startswith='test_').delete()

    # Test creating a PAT
    test_token = "noj_pat_test_secret"
    hash_val = PersonalAccessToken.hash_token(test_token)
    SCOPES = ['read:user', 'read:problems', 'write:submissions']

    # Use the add method
    test_pat = PersonalAccessToken.add(pat_id='test_001',
                                       name='Test Token',
                                       owner='test_user',
                                       hash_val=hash_val,
                                       scope=SCOPES,
                                       due_time=datetime.now(timezone.utc) +
                                       timedelta(days=30))

    # Test retrieving PAT
    retrieved = PersonalAccessToken(
        PersonalAccessToken.objects.get(pat_id='test_001'))
    assert retrieved.name == 'Test Token'
    assert retrieved.owner == 'test_user'
    assert retrieved.scope == SCOPES
    assert not retrieved.is_revoked

    # Test to_dict method (replaces _clean_token)
    cleaned = retrieved.to_dict()
    assert cleaned['Name'] == 'Test Token'
    assert cleaned['ID'] == 'test_001'
    assert cleaned['Owner'] == 'test_user'
    assert cleaned['Status'] == 'Active'
    assert cleaned['Scope'] == SCOPES

    # Test updating PAT
    UPDATED_SCOPES = ['read:user']
    retrieved.update(name='Updated Token', scope=UPDATED_SCOPES)
    updated = PersonalAccessToken(
        PersonalAccessToken.objects.get(pat_id='test_001'))
    assert updated.name == 'Updated Token'
    assert updated.scope == UPDATED_SCOPES

    # Test revoking PAT using update directly (simulating admin/owner action not via revoke method yet)
    # Note: real revoke logic is in .revoke() which requires a User object,
    # but here we test the model properties directly first.
    updated.update(is_revoked=True, revoked_by='admin')
    revoked = PersonalAccessToken(
        PersonalAccessToken.objects.get(pat_id='test_001'))
    assert revoked.is_revoked == True
    assert revoked.revoked_by == 'admin'

    # Test to_dict with revoked token
    cleaned_revoked = revoked.to_dict()
    assert cleaned_revoked['Status'] == 'Deactivated'

    # Test status property directly
    assert revoked.status == 'deactivated'

    # Test expired token status
    EXPIRED_SCOPES = ['read:courses']
    expired_pat = PersonalAccessToken.add(
        pat_id='test_002',
        name='Expired Token',
        owner='test_user',
        hash_val=PersonalAccessToken.hash_token('noj_pat_expired'),
        scope=EXPIRED_SCOPES,
        due_time=datetime.now(timezone.utc) - timedelta(days=1))

    assert expired_pat.status == 'due'
    cleaned_expired = expired_pat.to_dict()
    assert cleaned_expired['Status'] == 'Due'

    # Clean up
    PersonalAccessToken.objects(pat_id__startswith='test_').delete()
    print("✅ All MongoDB PAT tests passed!")


if __name__ == "__main__":
    test_mongodb_pat_integration()
