# IT 기술 트렌드의 논문-도서 반영 시차 분석

## 1. 개요

## 2. 데이터

### 2.1 현재 데이터 명세

| 항목 | 내용 |
|---|---|
| 파일 | `class3/data/yes24_it_mobile_list.csv` |
| 수집 대상 | 예스24 `국내도서 > IT 모바일` 및 하위 분류의 목록 상품 |
| 관측 단위 | 도서 상품 1개 |
| 수집일 | 2026-11-08 |
| 데이터 크기 | 46,081행, 18열 |
| 기본키 | `product_id`(중복 0건) |
| 발행월 범위 | 1980-01~2027-02 |
| 주 분석 대상 | 발행월 누락 30건과 예약출간 15건을 제외한 46,036건 |

이 자료는 국내 IT 출판시장 전체가 아니라 수집 시점에 예스24 목록에서 확인된 상품이다. 발행월은 목록에 표시된 연월이며 정확한 발행일은 포함하지 않는다. 논문 데이터는 아직 수집하지 않았다.

### 2.2 수집 컬럼

| 구분 | 컬럼 |
|---|---|
| 상품 식별 | `product_id`, `product_url` |
| 도서 정보 | `title`, `subtitle`, `contributors`, `publisher` |
| 발행 정보 | `publication_month`, `publication_date_raw` |
| 가격 정보 | `list_price`, `sale_price`, `discount_rate`, `points` |
| 독자 반응 | `average_rating`, `rating_count`, `sales_index` |
| 수집 정보 | `cover_url`, `source_category`, `collected_at` |

분석에 직접 영향을 주는 결측은 `publication_month` 30건, `subtitle` 28,795건, `publisher` 2건이다. 부제가 없는 도서는 제목만 사용한다.

### 2.3 사용 컬럼

| 사용 목적 | 원본 컬럼 | 처리 방법 |
|---|---|---|
| 상품 식별 | `product_id` | 중복 확인과 도서 수 집계 |
| 기술 주제 분류 | `title`, `subtitle` | 제목과 부제를 결합하여 기술 주제 판별 |
| 시간별 추세 | `publication_month` | 연도·월·분기 단위 집계 |
| 출판사 비교 | `publisher` | 출판사별 주제 반영 시차 비교 |

```json
{
  "topic_text": "title + subtitle",
  "publication_year": "publication_month의 연도",
  "publication_quarter": "publication_month의 분기",
  "topic_label": "도서별 기술 주제",
  "topic_share": "시기별 전체 도서 중 해당 주제 도서의 비율"
}
```

## 3. 본론

## 4. 결론
