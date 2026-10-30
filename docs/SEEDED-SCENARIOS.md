# Seed data catalogue

The dataset is deterministic in shape and uses dates relative to the day it is started. Resetting
restores this state.

## Dataset

- 4 Northstar Motors dealerships across Manchester, Stockport, Liverpool, and Bolton
- separate sales, service, and parts opening hours
- one upcoming bank-holiday exception for sales and service
- 60 used vehicles across BMW, MINI, Jaguar, Land Rover, Volvo, and Kia
- available, reserved, and sold vehicle states
- two vehicles whose price is available only on request
- 8 published new-car offers
- 8 workshop service types
- 21 days of test-drive and workshop slot data, excluding Sundays
- no workshop availability at Bolton for the first 7 seeded days
- 2 confirmed workshop bookings

## Stable records

These identifiers are restored by every reset:

| Purpose                  | Identifier             |
| ------------------------ | ---------------------- |
| Manchester dealership    | `northstar-manchester` |
| Stockport dealership     | `northstar-stockport`  |
| Liverpool dealership     | `northstar-liverpool`  |
| Bolton dealership        | `northstar-bolton`     |
| Available vehicle        | `veh-001`              |
| Reserved vehicle         | `veh-007`              |
| Sold vehicle             | `veh-013`              |
| Price-on-request vehicle | `veh-019`              |
| Full service             | `full-service`         |
| MOT                      | `mot`                  |
| First published offer    | `offer-01`             |
| First workshop booking   | `wsb-seeded-001`       |
| Second workshop booking  | `wsb-seeded-002`       |

Slot identifiers are stable after reset, but their calendar dates move forward so that they
remain useful. Query availability before using a slot.

## Existing workshop bookings

These bookings can be used with `POST /api/workshop-bookings/lookup`:

| Reference    | Surname | Registration | Phone         |
| ------------ | ------- | ------------ | ------------- |
| `WORK-10001` | Taylor  | `AB12 CDE`   | `07700900123` |
| `WORK-10002` | Morgan  | `XY34 ZZZ`   | `07700900456` |

## Notable seeded states

- `veh-019` has `pricePence: null`; its price is available on request.
- `veh-007` is reserved. Test-drive booking returns `VEHICLE_RESERVED`, while interest
  registration is accepted.
- `veh-013` is sold. Test-drive booking is unavailable, but a general sales enquiry can still be
  recorded.
- Bolton has no workshop availability during the first seven seeded days. Other dealerships and
  later dates have availability.
- The seeded bank-holiday exception gives sales reduced hours and closes the service department.
- Part-exchange estimates are deterministic for the same vehicle and condition details and
  include the applicable qualification.
