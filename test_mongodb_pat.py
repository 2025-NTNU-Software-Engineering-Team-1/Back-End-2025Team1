import pytest
from datetime import datetime, timezone, timedelta
from mongo.engine import PersonalAccessToken
from model.profile import _clean_token, get_pat_status
from model.utils.pat import hash_pat_token

def test_mongodb_pat_integration():
    """Test basic PAT functionality with MongoDB"""
    
    # Clean up any existing test data
    PersonalAccessToken.objects(pat_id__startswith='test_').delete()
    
    # Test creating a PAT
    test_token = "noj_pat_test_secret"
    test_pat = PersonalAccessToken(
        pat_id='test_001',
        name='Test Token',
        owner='test_user',
        hash=hash_pat_token(test_token),
        scope=['read', 'write'],
        due_time=datetime.now(timezone.utc) + timedelta(days=30),
        created_time=datetime.now(timezone.utc),
        is_revoked=False
    )
    test_pat.save()
    
    # Test retrieving PAT
    retrieved = PersonalAccessToken.objects.get(pat_id='test_001')
    assert retrieved.name == 'Test Token'
    assert retrieved.owner == 'test_user'
    assert retrieved.scope == ['read', 'write']
    assert not retrieved.is_revoked
    
    # Test _clean_token function
    cleaned = _clean_token(retrieved)
    assert cleaned['Name'] == 'Test Token'
    assert cleaned['ID'] == 'test_001'
    assert cleaned['Owner'] == 'test_user'
    assert cleaned['Status'] == ['Active']
    assert cleaned['Scope'] == ['read', 'write']
    
    # Test updating PAT
    retrieved.update(name='Updated Token', scope=['read'])
    updated = PersonalAccessToken.objects.get(pat_id='test_001')
    assert updated.name == 'Updated Token'
    assert updated.scope == ['read']
    
    # Test revoking PAT
    updated.update(is_revoked=True, revoked_by='admin')
    revoked = PersonalAccessToken.objects.get(pat_id='test_001')
    assert revoked.is_revoked == True
    assert revoked.revoked_by == 'admin'
    
    # Test _clean_token with revoked token
    cleaned_revoked = _clean_token(revoked)
    assert cleaned_revoked['Status'] == ['Revoked']
    
    # Test get_pat_status function directly
    assert get_pat_status(revoked) == 'revoked'
    
    # Test expired token status
    expired_pat = PersonalAccessToken(
        pat_id='test_002',
        name='Expired Token',
        owner='test_user',
        hash=hash_pat_token('noj_pat_expired'),
        scope=['read'],
        due_time=datetime.now(timezone.utc) - timedelta(days=1),  # Already expired
        created_time=datetime.now(timezone.utc),
        is_revoked=False
    )
    expired_pat.save()
    
    assert get_pat_status(expired_pat) == 'expired'
    cleaned_expired = _clean_token(expired_pat)
    assert cleaned_expired['Status'] == ['Expired']
    
    # Clean up
    PersonalAccessToken.objects(pat_id__startswith='test_').delete()
    print("✅ All MongoDB PAT tests passed!")

if __name__ == "__main__":
    test_mongodb_pat_integration()