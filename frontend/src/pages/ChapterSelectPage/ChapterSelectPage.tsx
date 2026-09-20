import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { fetchBooks } from '../../services/api'
import { useAppStore } from '../../store/useAppStore'
import type { Book } from '../../types'
import { chaptersForBook } from '../../data/bookChapters'
import { isChapterUnlocked, readChapterResults, readChapterStars } from '../../utils/chapterProgress'
import StatusScreen from '../../components/StatusScreen/StatusScreen'
import StarRow from '../../components/StarRow/StarRow'
import styles from './ChapterSelectPage.module.css'

export default function ChapterSelectPage() {
  const navigate = useNavigate()
  const { bookId } = useParams<{ bookId: string }>()
  const { selectedBook, selectBook } = useAppStore()
  const [book, setBook] = useState<Book | null>(selectedBook?.id === bookId ? selectedBook : null)
  const [isLoading, setIsLoading] = useState(!book)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!bookId || book) return
    setIsLoading(true)
    fetchBooks()
      .then((books) => {
        const found = books.find((item) => item.id === bookId) ?? null
        setBook(found)
        if (found) selectBook(found)
        if (!found) setError('Could not find this book.')
      })
      .catch(() => setError('Could not load chapters.'))
      .finally(() => setIsLoading(false))
  }, [book, bookId, selectBook])

  const stars = useMemo(() => readChapterStars(bookId ?? ''), [bookId])
  const results = useMemo(() => readChapterResults(bookId ?? ''), [bookId])
  const chapters = useMemo(() => chaptersForBook(book), [book])

  const startChapter = (chapterNumber: number) => {
    if (!book || !isChapterUnlocked(book, chapterNumber, stars, results)) return
    selectBook({ ...book, currentLesson: chapterNumber })
    navigate(`/learn/${book.id}?chapter=${chapterNumber}&restart=${Date.now()}`)
  }

  if (isLoading) {
    return (
      <main className={styles.page}>
        <StatusScreen isLoading />
      </main>
    )
  }

  if (error || !book) {
    return (
      <main className={styles.page}>
        <button className={styles.backButton} onClick={() => navigate('/home')} aria-label="Go home">←</button>
        <StatusScreen error={error} onRetry={() => window.location.reload()} />
      </main>
    )
  }

  return (
    <main className={styles.page}>
      <header className={styles.header}>
        <button className={styles.backButton} onClick={() => navigate('/home')} aria-label="Go home">←</button>
        <div>
          <h1>Select Chapter</h1>
          {book.title && <p>{book.title}</p>}
        </div>
      </header>

      <section className={styles.grid} aria-label="Choose a chapter">
        {chapters.map((chapter) => {
          const chapterStars = stars[String(chapter.chapterNumber)] ?? 0
          const unlocked = isChapterUnlocked(book, chapter.chapterNumber, stars, results)
          return (
            <button
              key={chapter.chapterNumber}
              className={`${styles.chapterCard} ${unlocked ? '' : styles.locked}`}
              onClick={() => startChapter(chapter.chapterNumber)}
              disabled={!unlocked}
            >
              <span className={styles.planet} />
              <strong>{chapter.label}</strong>
              <em>{unlocked ? chapter.theme : 'Finish the chapter before this one.'}</em>
              <StarRow lit={chapterStars} size={22} gap={3} className={styles.stars} />
            </button>
          )
        })}
      </section>
    </main>
  )
}
