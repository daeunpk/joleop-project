import { type CSSProperties, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAppStore } from '../../store/useAppStore'
import StatsBar from '../../components/StatsBar/StatsBar'
import StoryBookCover from '../../components/StoryBookCover/StoryBookCover'
import { ApiError, clearProfileSession, fetchHome, usesBackendApi } from '../../services/api'
import {
  DEFAULT_HOME_BACKGROUND_THEME_ID,
  findHomeBackgroundTheme,
} from '../../data/homeBackgroundThemes'
import { ICONS } from '../../constants/assets'
import { resolvePopoSpots, type PopoCustomization } from '../../data/popoItems'
import styles from './HomePage.module.css'

const HOME_THEME_KEY = 'yeongcha:home-background-theme'
const POPO_CUSTOMIZATION_KEY = 'yeongcha:popo-customization'

function readSelectedThemeId(): string {
  return window.localStorage.getItem(HOME_THEME_KEY) || DEFAULT_HOME_BACKGROUND_THEME_ID
}

function readPopoCustomization(): PopoCustomization {
  try {
    return JSON.parse(window.localStorage.getItem(POPO_CUSTOMIZATION_KEY) || '{}')
  } catch {
    return {}
  }
}

export default function HomePage() {
  const navigate = useNavigate()
  const { selectedBook, userStats } = useAppStore()
  const [selectedThemeId, setSelectedThemeId] = useState(readSelectedThemeId)
  const [popoCustomization, setPopoCustomization] = useState(readPopoCustomization)

  useEffect(() => {
    if (!usesBackendApi()) {
      navigate('/profiles', { replace: true })
      return
    }
    fetchHome()
      .then((home) => {
        useAppStore.setState({ userStats: home.stats })
        if (home.customization) {
          setSelectedThemeId(home.customization.selectedThemeId)
          setPopoCustomization(home.customization.selectedPopo)
        }
      })
      .catch((err: unknown) => {
        if (err instanceof ApiError && err.status === 401) {
          clearProfileSession()
          navigate('/profiles', { replace: true })
          return
        }
        console.warn('Could not load DB home data.', err)
      })
  }, [navigate])

  useEffect(() => {
    const syncSelectedTheme = () => setSelectedThemeId(readSelectedThemeId())
    const syncCustomization = () => {
      if (usesBackendApi()) {
        fetchHome()
          .then((home) => {
            useAppStore.setState({ userStats: home.stats })
            if (home.customization) {
              setSelectedThemeId(home.customization.selectedThemeId)
              setPopoCustomization(home.customization.selectedPopo)
            }
          })
          .catch(() => undefined)
        return
      }
      syncSelectedTheme()
      setPopoCustomization(readPopoCustomization())
    }
    window.addEventListener('storage', syncCustomization)
    window.addEventListener('focus', syncCustomization)
    return () => {
      window.removeEventListener('storage', syncCustomization)
      window.removeEventListener('focus', syncCustomization)
    }
  }, [])

  const selectedTheme = useMemo(() => findHomeBackgroundTheme(selectedThemeId), [selectedThemeId])

  // 화면 양옆에 하나씩 떠 있는 허브 버튼. 캐릭터는 가운데를 그대로 쓴다.
  const hubButtons = [
    { side: 'left' as const, slot: 'top' as const, path: '/review', label: 'Review', icon: ICONS.brain, tourId: 'nav-review' },
    { side: 'left' as const, slot: 'bottom' as const, path: '/mypage', label: 'Me', icon: ICONS.goal, tourId: 'nav-my' },
    { side: 'right' as const, slot: 'top' as const, path: '/customize', label: 'Style', icon: ICONS.armchair, tourId: 'customize' },
    { side: 'right' as const, slot: 'bottom' as const, path: '/books', label: 'Books', icon: ICONS.book, tourId: 'nav-books' },
  ]

  const handleStart = () => {
    if (!selectedBook) return
    navigate(`/books/${selectedBook.id}/chapters`)
  }

  return (
    <div
      className={styles.page}
      style={{
        '--home-background': selectedTheme.background,
        '--home-background-size': selectedTheme.backgroundSize,
        '--home-background-position': selectedTheme.backgroundPosition,
        '--room-floor': selectedTheme.floor,
        '--room-floor-shade': selectedTheme.floorShade,
      } as CSSProperties}
    >
      <header className={styles.header}>
        <StatsBar stats={userStats} tone="light" />
      </header>
      <span className={styles.headerShadow} aria-hidden="true" />

      <section className={styles.hero} aria-label="Home">
        <div className={styles.backgroundLayer} aria-hidden="true" />
        <span className={styles.footShadow} aria-hidden="true" />
        <div className={styles.mascotStage}>
          <img
            src="/images/HomePopo.webp"
            alt=""
            className={styles.mascot}
            onError={(event) => {
              event.currentTarget.src = '/images/HomeBearHands.webp'
            }}
          />
          {popoCustomization.hat && <span className={`${styles.popoHat} ${styles[`hat_${popoCustomization.hat}`]}`} />}
          {resolvePopoSpots(popoCustomization).map((item) => (
            <img key={item.id} src={item.spot} alt="" className={styles.popoSpot} aria-hidden="true" />
          ))}
          <button
            className={styles.heldBook}
            data-tour="held-book"
            onClick={() => navigate('/books')}
            aria-label={selectedBook ? 'Change book' : 'Pick a book'}
          >
            <StoryBookCover book={selectedBook} variant="held" />
          </button>
        </div>

        {hubButtons.map((item) => (
          <button
            key={item.path}
            className={[styles.hubButton, styles[`hub_${item.side}_${item.slot}`]].join(' ')}
            data-tour={item.tourId}
            onClick={() => navigate(item.path)}
          >
            <img src={item.icon} alt="" className={styles.hubIcon} aria-hidden="true" />
            <span className={styles.hubLabel}>{item.label}</span>
          </button>
        ))}
      </section>

      <section className={styles.panel}>
        {selectedBook && (
          <div className={styles.progressTrack}>
            <div
              className={styles.progressFill}
              style={{ width: `${selectedBook.progress * 100}%` }}
            />
          </div>
        )}

        <button
          className={styles.bookCard}
          data-tour="book"
          onClick={() => navigate('/books')}
          aria-label={selectedBook ? 'Change book' : 'Pick a book'}
        >
          <StoryBookCover book={selectedBook} variant="current" className={styles.bookCover} />
          <span className={styles.bookMeta}>
            <em>
              <img src={ICONS.bookmark} alt="" className={styles.metaIcon} aria-hidden="true" />
              {selectedBook ? 'Current Book' : 'Library'}
            </em>
            <strong>{selectedBook ? selectedBook.title : 'Choose a Book'}</strong>
            <span>{selectedBook ? selectedBook.currentText ?? 'Keep going!' : 'New stories are waiting.'}</span>
          </span>
        </button>

        <button
          className={styles.startButton}
          data-tour="start-learning"
          onClick={handleStart}
          disabled={!selectedBook}
        >
          <img src={ICONS.star} alt="" className={styles.startIcon} aria-hidden="true" />
          {selectedBook ? 'Start Adventure' : 'Choose Book First'}
        </button>
      </section>

    </div>
  )
}
