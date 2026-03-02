"""
MOR Emission Schedule Service

Calculates daily MOR emissions based on the Morpheus emission schedule.

https://github.com/MorpheusAIs/Docs/blob/main/!KEYDOCS%20README%20FIRST!/Token%20Emission%20Schedule.md
Emission Schedule:
- Start date: February 8, 2024
- Initial emission: 14,400 MOR per day
- Daily decline: 2.468994701 MOR per day
- End: Day 5,833 when emission reaches 0

Distribution:
- Capital Emission: 24%
- Code Emission: 24%
- Compute Emission: 24%
- Community Emission: 24%
- Protection Emission: 4%

Reference emission data (for validation):
Day  Date      Total Emission  Total Supply
1    02/08/24  14400.0         14400.0
2    02/09/24  14397.531       28797.531
3    02/10/24  14395.062       43192.593
4    02/11/24  14392.593       57585.186
"""

from typing import Optional
from decimal import Decimal, ROUND_HALF_UP
from datetime import date, datetime
from dataclasses import dataclass

from ..core.logging_config import get_core_logger

logger = get_core_logger()


@dataclass
class EmissionInfo:
    """Information about MOR emissions for a specific day."""
    day_number: int
    date: date
    total_emission: Decimal
    capital_emission: Decimal
    code_emission: Decimal
    compute_emission: Decimal
    community_emission: Decimal
    protection_emission: Decimal
    cumulative_supply: Decimal


class MOREmissionService:
    """
    Service for calculating MOR token emissions based on the emission schedule.
    
    The emission follows a linear decline:
    - Starts at 14,400 MOR/day on Feb 8, 2024
    - Decreases by 2.468994701 MOR each day
    - Reaches 0 on day 5,833
    
    Usage:
        service = MOREmissionService()
        today_emission = service.get_daily_emission()
        emission_info = service.get_emission_info()
    """
    
    # Emission schedule constants
    START_DATE = date(2024, 2, 8)  # Feb 8, 2024
    INITIAL_DAILY_EMISSION = Decimal("14400")  # MOR per day
    DAILY_DECLINE = Decimal("2.468994701")  # MOR decline per day
    END_DAY = 5833  # Day when emission reaches 0
    
    # Distribution percentages
    CAPITAL_PERCENT = Decimal("0.24")
    CODE_PERCENT = Decimal("0.24")
    COMPUTE_PERCENT = Decimal("0.24")
    COMMUNITY_PERCENT = Decimal("0.24")
    PROTECTION_PERCENT = Decimal("0.04")
    
    PERCENT_ACTUAL_DISTRIBUTION = Decimal("0.8") # 80% of the emission is distributed to builders
    
    def __init__(self):
        """Initialize the emission service."""
        self._emission_logger = logger.bind(component="mor_emission_service")
    
    def get_day_number(self, for_date: Optional[date] = None) -> int:
        """
        Get the day number in the emission schedule.
        
        Day 1 = Feb 8, 2024
        
        Args:
            for_date: Date to calculate for. Defaults to today.
            
        Returns:
            Day number (1-indexed)
        """
        if for_date is None:
            for_date = date.today()
        
        delta = for_date - self.START_DATE
        day_number = delta.days + 1  # Day 1 = Feb 8, 2024
        
        return max(1, day_number)  # Minimum day 1
    
    def get_daily_emission(self, for_date: Optional[date] = None) -> Decimal:
        """
        Get the total MOR emission for a specific day.
        
        Args:
            for_date: Date to calculate for. Defaults to today.
            
        Returns:
            Total MOR emission for the day (0 if past end date)
        """
        day_number = self.get_day_number(for_date)
        
        if day_number >= self.END_DAY:
            return Decimal("0")
        
        # Emission = 14400 - (day_number - 1) * 2.468994701
        # Day 1: 14400 - 0 = 14400
        # Day 2: 14400 - 2.468994701 = 14397.531...
        days_elapsed = day_number - 1
        emission = self.INITIAL_DAILY_EMISSION - (Decimal(str(days_elapsed)) * self.DAILY_DECLINE)
        
        # Ensure non-negative
        emission = max(Decimal("0"), emission)
        
        # Round to reasonable precision
        emission = emission.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        
        self._emission_logger.debug(
            "Calculated daily emission",
            day_number=day_number,
            for_date=str(for_date),
            emission=str(emission)
        )
        
        return emission
    
    def get_compute_emission(self, for_date: Optional[date] = None) -> Decimal:
        """
        Get the compute emission for a specific day (24% of total).
        
        This is typically what builders/stakers receive.
        
        Args:
            for_date: Date to calculate for. Defaults to today.
            
        Returns:
            Compute emission for the day
        """
        total = self.get_daily_emission(for_date)
        return (total * self.COMPUTE_PERCENT * self.PERCENT_ACTUAL_DISTRIBUTION).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    
    def get_emission_info(self, for_date: Optional[date] = None) -> EmissionInfo:
        """
        Get complete emission information for a specific day.
        
        Args:
            for_date: Date to calculate for. Defaults to today.
            
        Returns:
            EmissionInfo with all emission details
        """
        if for_date is None:
            for_date = date.today()
            
        day_number = self.get_day_number(for_date)
        total_emission = self.get_daily_emission(for_date)
        
        # Calculate category emissions
        capital = (total_emission * self.CAPITAL_PERCENT).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        code = (total_emission * self.CODE_PERCENT).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        compute = (total_emission * self.COMPUTE_PERCENT).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        community = (total_emission * self.COMMUNITY_PERCENT).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        protection = (total_emission * self.PROTECTION_PERCENT).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
        
        # Calculate cumulative supply up to this day
        cumulative = self._calculate_cumulative_supply(day_number)
        
        return EmissionInfo(
            day_number=day_number,
            date=for_date,
            total_emission=total_emission,
            capital_emission=capital,
            code_emission=code,
            compute_emission=compute,
            community_emission=community,
            protection_emission=protection,
            cumulative_supply=cumulative
        )
    
    def _calculate_cumulative_supply(self, up_to_day: int) -> Decimal:
        """
        Calculate total MOR supply up to a specific day.
        
        Uses the formula for sum of arithmetic sequence:
        Sum = n/2 * (first_term + last_term)
        
        Args:
            up_to_day: Day number to calculate up to
            
        Returns:
            Cumulative supply
        """
        if up_to_day <= 0:
            return Decimal("0")
        
        # Limit to end day
        n = min(up_to_day, self.END_DAY - 1)
        
        # First term (day 1 emission)
        first_term = self.INITIAL_DAILY_EMISSION
        
        # Last term (day n emission)
        last_term = self.INITIAL_DAILY_EMISSION - (Decimal(str(n - 1)) * self.DAILY_DECLINE)
        last_term = max(Decimal("0"), last_term)
        
        # Sum = n/2 * (first + last)
        cumulative = (Decimal(str(n)) / Decimal("2")) * (first_term + last_term)
        
        return cumulative.quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)
    
# Global service instance
_mor_emission_service: Optional[MOREmissionService] = None


def get_mor_emission_service() -> MOREmissionService:
    """
    Get the global MOR emission service instance.
    
    Returns:
        MOREmissionService singleton
    """
    global _mor_emission_service
    if _mor_emission_service is None:
        _mor_emission_service = MOREmissionService()
    return _mor_emission_service


def set_mor_emission_service(service: MOREmissionService) -> None:
    """
    Set the global MOR emission service instance.
    
    Args:
        service: MOREmissionService instance to use globally
    """
    global _mor_emission_service
    _mor_emission_service = service

