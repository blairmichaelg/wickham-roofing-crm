"""
Climate Lookup utilities for determining regional code requirements.
"""

# Southern/coastal states where ice barriers are definitively NOT required
# per general historic climate data and IRC R301.2(1) abstractions.
ICE_BARRIER_NOT_REQUIRED = {
    "AL", "AR", "AZ", "FL", "GA", "HI", "LA", "MS", "NM", "SC", "TX"
}

# Northern-tier states where ice barriers are definitively REQUIRED.
ICE_BARRIER_REQUIRED = {
    "AK", "CO", "CT", "IA", "ID", "IL", "IN", "MA", "ME", "MI", 
    "MN", "MT", "ND", "NE", "NH", "NY", "OH", "PA", "RI", "SD", 
    "VT", "WI", "WY"
}

def is_ice_barrier_required(state: str) -> bool | None:
    """
    Returns True if an ice barrier is required for the given US state abbreviation.
    Returns False if it is explicitly not required.
    Returns None if the state is ambiguous/middle-latitude and requires manual review.
    """
    state_upper = state.strip().upper()
    
    if state_upper in ICE_BARRIER_REQUIRED:
        return True
    if state_upper in ICE_BARRIER_NOT_REQUIRED:
        return False
        
    # Ambiguous or non-matching states require manual review
    return None
