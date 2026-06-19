#!/usr/bin/env python3
"""
Cost Model for AIOps Platform
=============================
This script implements the break-even cost-benefit analysis for implementing
an AIOps platform. It calculates the ROI, monthly value saved, payback months,
and returns a verdict.

Task: W3-D3 Cost Model Implementation
"""

def is_worth_it(
    num_services: int,
    incidents_per_month: int,
    avg_incident_duration_hours: float,
    downtime_cost_per_hour: float,
    expected_mttr_reduction_pct: float = 0.4,
    aiops_monthly_cost: float = 15_000,
) -> dict:
    """
    Computes the financial viability of deploying an AIOps solution.

    Returns:
      {
        "monthly_value": float,
        "monthly_cost": float,
        "roi": float,
        "payback_months": float,  # or float('inf')
        "verdict": "worth_it" | "marginal" | "not_worth_it"
      }
    Verdict rule:
      roi > 1.5 → worth_it
      1.0 < roi ≤ 1.5 → marginal
      roi ≤ 1.0 → not_worth_it
    """
    monthly_downtime_hours = incidents_per_month * avg_incident_duration_hours
    monthly_value = (
        monthly_downtime_hours
        * expected_mttr_reduction_pct
        * downtime_cost_per_hour
    )
    
    roi = monthly_value / aiops_monthly_cost if aiops_monthly_cost > 0 else float('inf')
    payback_months = aiops_monthly_cost / monthly_value if monthly_value > 0 else float('inf')
    
    if roi > 1.5:
        verdict = "worth_it"
    elif roi > 1.0:
        verdict = "marginal"
    else:
        verdict = "not_worth_it"
        
    return {
        "monthly_value": float(monthly_value),
        "monthly_cost": float(aiops_monthly_cost),
        "roi": float(roi),
        "payback_months": float(payback_months),
        "verdict": verdict,
    }


if __name__ == "__main__":
    print("Scenario 1 (20 services, 2 incidents/mo, $10k/hr downtime, $15k cost):")
    print(is_worth_it(num_services=20, incidents_per_month=2,
                      avg_incident_duration_hours=1, downtime_cost_per_hour=10_000,
                      aiops_monthly_cost=15_000))
    print()

    print("Scenario 2 (100 services, 5 incidents/mo, $20k/hr downtime, $25k cost):")
    print(is_worth_it(num_services=100, incidents_per_month=5,
                      avg_incident_duration_hours=2, downtime_cost_per_hour=20_000,
                      aiops_monthly_cost=25_000))
    print()

    # Scenario 3: Large E-commerce Platform (e.g. Shopee / Lazada VN scale)
    # Justification:
    # An e-commerce platform processing thousands of orders per minute faces massive direct revenue
    # losses when checkouts or payment gateways go down. In addition, marketing campaigns (like 11.11 / 12.12),
    # customer acquisition costs (CAC) wasted due to bounce rates, and SRE operational fatigue add to this.
    # We estimate a downtime cost of $50,000/hour as a conservative average across peak and non-peak hours.
    # With 10 major incidents per month averaging 1.5 hours each, and AIOps cost of $60,000/month:
    print("Scenario 3 (Large E-commerce VN Scale, 500 services, 10 incidents/mo, $50k/hr downtime, $60k cost):")
    print(is_worth_it(num_services=500, incidents_per_month=10,
                      avg_incident_duration_hours=1.5, downtime_cost_per_hour=50_000,
                      aiops_monthly_cost=60_000))
